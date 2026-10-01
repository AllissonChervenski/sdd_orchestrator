import json
from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from orchestrator.config.models import AgentResult, Config, ValidationResult
from orchestrator.storage.sqlite import StateStore
from orchestrator.agents.runner import AgentRunner
from orchestrator.validation.parser import parse_validation, sanitize_text
from orchestrator.workflow.driver import _create_and_validate, run_sdd_workflow, WorkflowBlocked
from orchestrator.tdd import execute_tdd_task
from orchestrator.workflow.tdd import TDDTask
from orchestrator.cli import status, inspect


def _setup_store_and_workflow(tmp_path, feature="test-feature"):
    db_path = tmp_path / ".orchestrator" / "state" / "orchestrator.sqlite3"
    store = StateStore(db_path)
    wid = store.create_workflow(
        feature,
        {
            "stage": "PLANNED",
            "workspace": str(tmp_path),
            "first_real_run": False,
            "attempts": {},
            "completed_tasks": [],
            "blocked_tasks": [],
            "validation_results": [],
            "verification_results": [],
            "state_schema_version": store.SCHEMA_VERSION,
        },
    )
    return store, wid


class DummyRouter:
    def route(self, role, **kwargs):
        return SimpleNamespace(provider="codex", model="gpt-6-luna")


def test_sanitize_text_removes_chain_of_thought_and_secrets():
    raw = (
        "<thought>\nThinking secret thoughts about key: api_key=SECRET12345678\n</thought>\n"
        "<thinking>Another private thought</thinking>\n"
        '{"status": "PASS", "summary": "ok", "token": "token=MY_SECRET_TOKEN_9999", '
        '"auth": "Bearer secret_jwt_token_12345678"}'
    )
    sanitized = sanitize_text(raw)
    assert "<thought>" not in sanitized
    assert "<thinking>" not in sanitized
    assert "SECRET12345678" not in sanitized
    assert "MY_SECRET_TOKEN_9999" not in sanitized
    assert "secret_jwt_token_12345678" not in sanitized
    assert "[REDACTED]" in sanitized
    assert '"status": "PASS"' in sanitized


def test_pass_persists_result(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)
    agent_res = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Constitution verified"}')
    val_res = ValidationResult("PASS", [], "Constitution verified", "constitution_validator", "gpt-6-luna", datetime.now(timezone.utc).isoformat(), agent_res.stdout)

    runner.record_validation(agent_res, val_res, stage="CONSTITUTION_VALIDATE")

    wf = store.get_workflow(wid)
    assert len(wf["state"]["validation_results"]) == 1
    res = wf["state"]["validation_results"][0]
    assert res["status"] == "PASS"
    assert res["stage"] == "CONSTITUTION_VALIDATE"
    assert res["role"] == "constitution_validator"
    assert res["provider"] == "codex"
    assert res["model"] == "gpt-6-luna"
    assert res["reason"] == "Constitution verified"
    assert res["issues"] == []
    assert res["timestamp"]
    assert "raw_response" in res

    # Check sqlite validations table
    rows = store.list_validations(wid)
    assert len(rows) == 1
    assert rows[0]["status"] == "PASS"

    # Check report on disk
    report_file = tmp_path / ".orchestrator" / "reports" / f"{wid}.json"
    assert report_file.is_file()
    report = json.loads(report_file.read_text())
    assert len(report["validation_results"]) == 1
    assert report["validation_results"][0]["status"] == "PASS"


def test_revise_persists_before_blocking(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    artifact = tmp_path / "spec.md"
    artifact.write_text("# Spec")

    class RevisingRunner:
        def __init__(self):
            self.store = store
            self.workflow_id = wid
            self.validation_results = []
            self.call_count = 0

        def run(self, role, prompt, **kwargs):
            if role == "specification":
                artifact.write_text(f"# Spec v{self.call_count}\n\nFR-001: Persist a provider listing.\n")
                return AgentResult("codex", "gpt-6-luna", role, True, stdout="SpecKit file written")
            self.call_count += 1
            issues = [f"Missing details {self.call_count} in spec.md (FR-1)"]
            return AgentResult("opencode", "gpt-6-luna", role, True, stdout=json.dumps({"status": "REVISE", "issues": issues, "summary": f"Needs revision attempt {self.call_count}"}))

        def record_validation(self, agent_res, val_res, stage=None, evidence=None):
            entry = {
                "stage": stage or "SPECIFICATION_VALIDATE",
                "role": agent_res.role,
                "provider": agent_res.provider,
                "model": agent_res.model,
                "status": val_res.status,
                "reason": val_res.summary,
                "issues": list(val_res.issues),
                "evidence": evidence or {},
                "raw_response": val_res.raw_output,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.validation_results.append(entry)
            self.store.record_validation(self.workflow_id, entry)

    runner = RevisingRunner()
    with pytest.raises(WorkflowBlocked) as exc_info:
        _create_and_validate(runner, "specification", "specification_validator", artifact, "Feature prompt", tmp_path, 10, retries=2)

    assert "retry limit exceeded" in str(exc_info.value)
    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 2
    assert results[0]["status"] == "REVISE"
    assert results[0]["reason"] == "Needs revision attempt 1"
    assert len(results[0]["issues"]) == 1
    assert results[1]["status"] == "REVISE"
    assert results[1]["reason"] == "Needs revision attempt 2"


def test_blocked_persists_before_blocking_canary_reproduction(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    const_file = tmp_path / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text("# Existing Constitution\n\nAll validation must use an independent provider.\n")

    class CanaryRunner:
        def __init__(self):
            self.store = store
            self.workflow_id = wid
            self.validation_results = []

        def run(self, role, prompt, **kwargs):
            issues = ["Independent verification violated in constitution.md (C-1)"]
            return AgentResult(
                "codex",
                "gpt-6-luna",
                role,
                True,
                stdout=json.dumps({"status": "BLOCKED", "issues": issues, "summary": "Existing constitution violates independent verification requirement"}),
            )

        def record_validation(self, agent_res, val_res, stage=None, evidence=None):
            entry = {
                "stage": stage or "CONSTITUTION_VALIDATE",
                "role": agent_res.role,
                "provider": agent_res.provider,
                "model": agent_res.model,
                "status": val_res.status,
                "reason": val_res.summary,
                "issues": list(val_res.issues),
                "evidence": evidence or {},
                "raw_response": val_res.raw_output,

                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.validation_results.append(entry)
            self.store.record_validation(self.workflow_id, entry)

    runner = CanaryRunner()
    harness = SimpleNamespace(requirement_results=[])
    cfg = Config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow("feature", tmp_path, runner, harness, cfg, tmp_path / ".orchestrator" / "runs" / wid, store=store)

    assert "constitution_validator BLOCKED" in str(exc_info.value)
    assert "Existing constitution violates independent verification requirement" in str(exc_info.value)

    # Verify state and report
    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 1
    assert results[0]["status"] == "BLOCKED"
    assert results[0]["role"] == "constitution_validator"
    assert results[0]["provider"] == "codex"
    assert results[0]["model"] == "gpt-6-luna"
    assert results[0]["reason"] == "Existing constitution violates independent verification requirement"
    assert len(results[0]["issues"]) == 1
    assert results[0]["issues"][0] == "Independent verification violated in constitution.md (C-1)"


    # Verify report JSON
    report_file = tmp_path / ".orchestrator" / "reports" / f"{wid}.json"
    assert report_file.is_file()
    report = json.loads(report_file.read_text())
    assert len(report["validation_results"]) == 1
    assert report["validation_results"][0]["status"] == "BLOCKED"


def test_parse_error_is_registered_distinctly(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    prose_output = "I have reviewed this document and in my opinion it looks fairly reasonable, but I forgot JSON."
    parsed = parse_validation(prose_output, "constitution_validator", "gpt-6-luna")

    assert parsed.status == "PARSE_ERROR"
    assert parsed.status != "BLOCKED"
    assert parsed.status != "REVISE"

    agent_res = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout=prose_output)
    runner.record_validation(agent_res, parsed, stage="CONSTITUTION_VALIDATE")

    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 1
    assert results[0]["status"] == "PARSE_ERROR"
    assert results[0]["reason"] == "Could not parse strict validation output"
    assert prose_output in results[0]["raw_response"]


def test_multiple_results_accumulated_append_only(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    # 1. Constitution validator PASS
    r1 = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Const ok"}')
    v1 = parse_validation(r1.stdout, "constitution_validator", r1.model)
    runner.record_validation(r1, v1, stage="CONSTITUTION_VALIDATE")

    # 2. Spec validator REVISE
    r2 = AgentResult("opencode", "gpt-5-turbo", "specification_validator", True, stdout='{"status":"REVISE","issues":["Clarify FR-1"],"summary":"Clarify FR-1"}')
    v2 = parse_validation(r2.stdout, "specification_validator", r2.model)
    runner.record_validation(r2, v2, stage="SPECIFICATION_VALIDATE")

    # 3. Spec validator PASS
    r3 = AgentResult("opencode", "gpt-5-turbo", "specification_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Spec ok"}')
    v3 = parse_validation(r3.stdout, "specification_validator", r3.model)
    runner.record_validation(r3, v3, stage="SPECIFICATION_VALIDATE")

    # 4. Plan validator PARSE_ERROR
    r4 = AgentResult("codex", "gpt-6-luna", "plan_validator", True, stdout="invalid prose")
    v4 = parse_validation(r4.stdout, "plan_validator", r4.model)
    runner.record_validation(r4, v4, stage="PLAN_VALIDATE")

    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 4
    assert [r["status"] for r in results] == ["PASS", "REVISE", "PASS", "PARSE_ERROR"]
    assert [r["role"] for r in results] == [
        "constitution_validator",
        "specification_validator",
        "specification_validator",
        "plan_validator",
    ]


def test_report_json_contains_results(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    r = AgentResult("codex", "gpt-6-luna", "tasks_validator", True, stdout='{"status":"PASS","issues":[],"summary":"tasks ok"}')
    v = parse_validation(r.stdout, "tasks_validator", r.model)
    runner.record_validation(r, v, stage="TASKS_VALIDATE")

    report_path = tmp_path / ".orchestrator" / "reports" / f"{wid}.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text())
    assert "validation_results" in report
    assert len(report["validation_results"]) == 1
    entry = report["validation_results"][0]
    for required_key in ("stage", "role", "provider", "model", "status", "reason", "issues", "evidence", "raw_response", "timestamp"):
        assert required_key in entry


def test_inspect_shows_issues_and_reason(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    v = ValidationResult("REVISE", ["Missing architecture diagram"], "Plan lacks diagrams", "plan_validator", "gpt-6-luna", datetime.now(timezone.utc).isoformat(), "raw")
    r = AgentResult("codex", "gpt-6-luna", "plan_validator", True, stdout="raw")
    runner.record_validation(r, v, stage="PLAN_VALIDATE")

    inspect(Namespace(workflow_id=wid))
    captured = capsys.readouterr().out
    data = json.loads(captured)

    assert "workflow" in data
    val_results = data["workflow"]["state"]["validation_results"]
    assert len(val_results) == 1
    assert val_results[0]["reason"] == "Plan lacks diagrams"
    assert val_results[0]["issues"][0] == "Missing architecture diagram"


def test_status_shows_last_relevant_result(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    # First result PASS
    r1 = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Const ok"}')
    v1 = parse_validation(r1.stdout, "constitution_validator", r1.model)
    runner.record_validation(r1, v1, stage="CONSTITUTION_VALIDATE")

    # Second result BLOCKED
    r2 = AgentResult("opencode", "gpt-5-turbo", "specification_validator", True, stdout='{"status":"BLOCKED","issues":["Spec rejected: critical defect"],"summary":"Spec rejected"}')
    v2 = parse_validation(r2.stdout, "specification_validator", r2.model)
    runner.record_validation(r2, v2, stage="SPECIFICATION_VALIDATE")

    status(Namespace(workflow_id=wid))
    captured = capsys.readouterr().out
    data = json.loads(captured)

    assert "last_validation_result" in data
    last = data["last_validation_result"]
    assert last["role"] == "specification_validator"
    assert last["status"] == "BLOCKED"
    assert last["reason"] == "Spec rejected"


def test_resume_preserves_validation_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    # Initial validation before block
    r1 = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Pass initial"}')
    v1 = parse_validation(r1.stdout, "constitution_validator", r1.model)
    runner.record_validation(r1, v1, stage="CONSTITUTION_VALIDATE")

    # Block workflow
    wf = store.get_workflow(wid)
    store.update_workflow(wid, "BLOCKED", {**wf["state"], "stage": "BLOCKED", "reason": "Blocked on purpose"})

    # Resume workflow and run a second validator
    resumed_wf = store.get_workflow(wid)
    assert len(resumed_wf["state"]["validation_results"]) == 1

    r2 = AgentResult("opencode", "gpt-5-turbo", "specification_validator", True, stdout='{"status":"PASS","issues":[],"summary":"Pass after resume"}')
    v2 = parse_validation(r2.stdout, "specification_validator", r2.model)
    runner.record_validation(r2, v2, stage="SPECIFICATION_VALIDATE")

    final_wf = store.get_workflow(wid)
    results = final_wf["state"]["validation_results"]
    assert len(results) == 2
    assert results[0]["reason"] == "Pass initial"
    assert results[1]["reason"] == "Pass after resume"


def test_crash_after_persistence_does_not_lose_diagnosis(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    r = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout='{"status":"BLOCKED","issues":["Fatal rule violation: C1"],"summary":"Fatal constitutional flaw"}')
    v = parse_validation(r.stdout, "constitution_validator", r.model)
    runner.record_validation(r, v, stage="CONSTITUTION_VALIDATE")

    # Simulate crash before workflow completes or checkpoints
    del runner

    # New connection from fresh process
    new_store = StateStore(tmp_path / ".orchestrator" / "state" / "orchestrator.sqlite3")
    wf = new_store.get_workflow(wid)
    assert len(wf["state"]["validation_results"]) == 1
    assert wf["state"]["validation_results"][0]["reason"] == "Fatal constitutional flaw"
    assert wf["state"]["validation_results"][0]["issues"][0] == "Fatal rule violation: C1"

    # Report JSON on disk also persisted
    report = json.loads((tmp_path / ".orchestrator" / "reports" / f"{wid}.json").read_text())
    assert len(report["validation_results"]) == 1
    assert report["validation_results"][0]["reason"] == "Fatal constitutional flaw"



def test_all_seven_validators_covered(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    roles = [
        "constitution_validator",
        "specification_validator",
        "plan_validator",
        "tasks_validator",
        "test_validator",
        "code_reviewer",
        "final_reviewer",
    ]

    for role in roles:
        r = AgentResult("codex", "gpt-6-luna", role, True, stdout=json.dumps({"status": "PASS", "issues": [], "summary": f"{role} passed"}))
        v = parse_validation(r.stdout, role, r.model)
        runner.record_validation(r, v)

    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == len(roles)
    recorded_roles = [item["role"] for item in results]
    assert recorded_roles == roles


def test_tdd_test_validator_and_code_reviewer_recorded_in_store(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)

    class TddRunner:
        def __init__(self):
            self.store = store
            self.workflow_id = wid
            self.validation_results = []

        def run(self, role, prompt, **kwargs):
            if role == "test_designer":
                if "RED:" in prompt:
                    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
                    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok(): assert False")
                return AgentResult("codex", "gpt-6-luna", role, True, stdout=json.dumps({
                    "task_id": "T1",
                    "requirement_ids": ["REQ-1"],
                    "acceptance_criteria_ids": ["AC-1"],
                    "created_tests": ["tests/test_sample.py"],
                    "test_commands": [["python", "-m", "pytest", "tests/test_sample.py"]]
                }))
            if role in {"test_validator", "code_reviewer"}:
                return AgentResult("opencode", "gpt-5-turbo", role, True, stdout=json.dumps({
                    "status": "PASS", "issues": [], "summary": f"{role} approved"
                }))
            if role == "coder":
                (tmp_path / "src").mkdir(exist_ok=True)
                (tmp_path / "src" / "sample.py").write_text("def ok(): pass")
                return AgentResult("codex", "gpt-6-luna", role, True, stdout="coded")
            return AgentResult("codex", "gpt-6-luna", role, True, stdout="ok")

        def record_validation(self, agent_res, val_res, stage=None, evidence=None):
            entry = {
                "stage": stage or agent_res.role.upper(),
                "role": agent_res.role,
                "provider": agent_res.provider,
                "model": agent_res.model,
                "status": val_res.status,
                "reason": val_res.summary,
                "issues": list(val_res.issues),
                "evidence": evidence or {},

                "raw_response": val_res.raw_output,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.validation_results.append(entry)
            self.store.record_validation(self.workflow_id, entry)

    runner = TddRunner()
    task = TDDTask("T1", ["REQ-1"], ["AC-1"], "UNIT")
    harness = SimpleNamespace(
        run_red=lambda cmd, **kw: SimpleNamespace(classification="EXPECTED_FAILURE", command=cmd, exit_code=1, stdout="", stderr="", cause=None),
        run_command=lambda cmd, **kw: SimpleNamespace(classification="PASS", command=cmd, exit_code=0, stdout="", stderr="", success=True, duration=0.1, status="PASS"),
    )

    execute_tdd_task(
        task,
        runner,
        harness,
        tmp_path,
        task_test_command=["python -m pytest"],
        regression_commands=[["python", "-m", "pytest"]],
        max_attempts=1,
        workflow_dir=tmp_path / ".orchestrator" / "runs" / wid,
        task_data={"id": "T1", "requirements": ["REQ-1"], "acceptance_criteria": ["AC-1"]},
    )
    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) >= 2
    recorded_roles = [r["role"] for r in results]
    assert "test_validator" in recorded_roles
    assert "code_reviewer" in recorded_roles
    assert any(r["stage"] == "RED_VALIDATE" for r in results)
    assert any(r["stage"] == "CODE_REVIEW" for r in results)
