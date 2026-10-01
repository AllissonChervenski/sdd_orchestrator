import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orchestrator.config.models import AgentResult, Config
from orchestrator.agents.roles import ROLES
from orchestrator.agents.runner import AgentRunner, load_prompt
from orchestrator.interactive import InteractiveGate
from orchestrator.providers.codex import CodexProvider
from orchestrator.providers.agy import AgyProvider
from orchestrator.storage.sqlite import StateStore
from orchestrator.validation.parser import parse_validation, sanitize_text
from orchestrator.workflow.driver import _call_gate, run_sdd_workflow, WorkflowBlocked


def _setup_store(tmp_path, feature="test-contract"):
    db_path = tmp_path / ".orchestrator" / "state" / "orchestrator.sqlite3"
    store = StateStore(db_path)
    wid = store.create_workflow(
        feature,
        {
            "stage": "CONSTITUTION_VALIDATE",
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
    def __init__(self, provider="codex", model="gpt-6-luna"):
        self._route = SimpleNamespace(
            provider=provider,
            model=model,
            independence="different_provider",
            selection_mode="role_binding",
            reason=None,
        )

    def route(self, role, **kwargs):
        return self._route



# --- 1. Canonical contract: PASS, REVISE, BLOCKED, multiple issues, summary vs reason ---


def test_canonical_contract_pass_with_empty_issues():
    payload = '{"status": "PASS", "summary": "All checks passed", "issues": []}'
    res = parse_validation(payload, "constitution_validator")
    assert res.status == "PASS"
    assert res.summary == "All checks passed"
    assert res.reason == "All checks passed"
    assert res.issues == []


def test_canonical_contract_revise_with_list_of_strings():
    payload = json.dumps({
        "status": "REVISE",
        "summary": "Fixes needed",
        "issues": [
            "Missing requirement FR-001 in spec",
            "Ambiguous error behavior in section 3",
        ],
    })
    res = parse_validation(payload, "specification_validator")
    assert res.status == "REVISE"
    assert res.summary == "Fixes needed"
    assert len(res.issues) == 2
    assert res.issues[0] == "Missing requirement FR-001 in spec"
    assert res.issues[1] == "Ambiguous error behavior in section 3"


def test_canonical_contract_blocked_with_list_of_strings():
    payload = json.dumps({
        "status": "BLOCKED",
        "summary": "Constitutional conflict",
        "issues": ["Violates non-tampering principle"],
    })
    res = parse_validation(payload, "constitution_validator")
    assert res.status == "BLOCKED"
    assert res.summary == "Constitutional conflict"
    assert res.issues == ["Violates non-tampering principle"]


def test_canonical_contract_accepts_reason_field():
    payload = json.dumps({
        "status": "BLOCKED",
        "reason": "Constitution not available",
        "issues": ["A Constitution não estava disponível ao validator."],
        "evidence": {},
    })
    res = parse_validation(payload, "constitution_validator")
    assert res.status == "BLOCKED"
    assert res.summary == "Constitution not available"
    assert res.reason == "Constitution not available"
    assert res.issues == ["A Constitution não estava disponível ao validator."]


def test_canonical_contract_non_string_issues_yields_parse_error():
    # Issue item is a dict (the bug that caused the incident)
    payload_dict = json.dumps({
        "status": "BLOCKED",
        "summary": "Failed",
        "issues": [{"id": "C-1", "description": "some error"}],
    })
    res1 = parse_validation(payload_dict, "constitution_validator")
    assert res1.status == "PARSE_ERROR"
    assert res1.summary == "Malformed issue schema"

    # Issue item is an int
    payload_int = json.dumps({
        "status": "REVISE",
        "summary": "Failed",
        "issues": [42],
    })
    res2 = parse_validation(payload_int, "specification_validator")
    assert res2.status == "PARSE_ERROR"
    assert res2.summary == "Malformed issue schema"

    # Issues is not a list
    payload_str = '{"status": "PASS", "summary": "ok", "issues": "not-a-list"}'
    res3 = parse_validation(payload_str, "plan_validator")
    assert res3.status == "PARSE_ERROR"
    assert res3.summary == "Could not parse strict validation output"

    # Issue item is None
    payload_none = json.dumps({"status": "REVISE", "summary": "failed", "issues": [None]})
    res4 = parse_validation(payload_none, "tasks_validator")
    assert res4.status == "PARSE_ERROR"
    assert res4.summary == "Malformed issue schema"


def test_contract_identical_across_codex_agy_and_opencode():
    contract = {
        "status": "PASS",
        "summary": "Verification successful",
        "issues": [],
    }

    # Codex JSONL format
    codex_jsonl = "\n".join([
        json.dumps({"type": "thread.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(contract)}}),
        json.dumps({"type": "turn.completed"}),
    ])
    res_codex = parse_validation(codex_jsonl, "constitution_validator", provider="codex")

    # AGY markdown fenced JSON
    agy_fenced = f"Here is the report:\n```json\n{json.dumps(contract)}\n```\n"
    res_agy = parse_validation(agy_fenced, "constitution_validator", provider="agy")

    # OpenCode raw JSON
    opencode_raw = json.dumps(contract)
    res_opencode = parse_validation(opencode_raw, "constitution_validator", provider="opencode")

    for res in (res_codex, res_agy, res_opencode):
        assert res.status == "PASS"
        assert res.summary == "Verification successful"
        assert res.reason == "Verification successful"
        assert res.issues == []


# --- 2. Injected artifacts: Constitution and all validators ---


def test_constitution_validator_receives_real_constitution_text():
    const_text = "# Project Constitution\n## Principle 1: Determinism\nAll checks must be reproducible."
    prompt = load_prompt("constitution_validator", feature="Add provider-summary command", artifact=const_text)

    assert "CONSTITUTION TO VALIDATE:" in prompt
    assert const_text in prompt
    assert "FEATURE REQUEST:" in prompt
    assert "Add provider-summary command" in prompt


def test_constitution_validator_prompt_instructs_no_shell_and_no_commands():
    prompt = load_prompt("constitution_validator", feature="feat", artifact="const")
    assert "Do not attempt to read files via shell or execute any commands." in prompt
    assert "Analyze only the provided artifacts and context below." in prompt
    assert '{"status":"PASS|REVISE|BLOCKED","summary":"...","issues":[]}' in prompt


def test_files_allowed_empty_does_not_prevent_validation_because_artifact_injected(tmp_path):
    store, wid = _setup_store(tmp_path)
    const_file = tmp_path / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    real_const = "# Living Constitution\nRule 1: No tampering"
    const_file.write_text(real_const)

    received_prompts = []

    class InjectedRunner:
        def __init__(self):
            self.store = store
            self.workflow_id = wid
            self.validation_results = []

        def run(self, role, prompt, **kwargs):
            received_prompts.append((role, prompt))
            # Validator returns PASS purely from reading injected prompt
            if role == "constitution_validator":
                assert real_const in prompt
                assert "CONSTITUTION TO VALIDATE:" in prompt
            return AgentResult(
                "codex",
                "gpt-6-luna",
                role,
                True,
                stdout=json.dumps({"status": "PASS", "summary": "Constitution conforms", "issues": []}),
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

    gate_calls = []

    def gate_callback(stage, role, files=(), commands=(), attempt=1, artifacts=()):
        gate_calls.append({"stage": stage, "role": role, "files": list(files), "artifacts": list(artifacts)})
        return True

    runner = InjectedRunner()
    harness = SimpleNamespace(requirement_results=[])
    cfg = Config()

    # Run workflow up to constitution validation (spec.md missing will abort afterwards, which is fine)
    with pytest.raises(WorkflowBlocked):
        run_sdd_workflow(
            "Feature: provider-summary",
            tmp_path,
            runner,
            harness,
            cfg,
            tmp_path / ".orchestrator" / "runs" / wid,
            store=store,
            gate_callback=gate_callback,
        )

    # 1. Gate inspection proves files_allowed is [] and artifacts_supplied contains constitution
    assert len(gate_calls) >= 1
    const_gate = next(g for g in gate_calls if g["role"] == "constitution_validator")
    assert const_gate["files"] == []
    assert any(".specify/memory/constitution.md" in a for a in const_gate["artifacts"])

    # 2. Prompt contains the real constitution
    assert len(received_prompts) >= 1
    role, prompt = received_prompts[0]
    assert role == "constitution_validator"
    assert real_const in prompt


def test_provider_mock_attempting_shell_is_not_necessary_in_normal_flow(tmp_path):
    store, wid = _setup_store(tmp_path)
    const_file = tmp_path / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text("# Constitution Content")

    class ZeroShellProvider:
        def run(self, prompt, role, model=None, cwd=None, timeout=None, permissions=None):
            # Assert no shell is required: prompt contains everything needed
            assert "CONSTITUTION TO VALIDATE:" in prompt
            assert "# Constitution Content" in prompt
            # Return valid canonical JSON without any external tool/shell calls
            return AgentResult(
                "codex",
                "gpt-6-luna",
                role,
                True,
                stdout=json.dumps({"status": "PASS", "summary": "Valid constitution", "issues": []}),
            )

    runner = AgentRunner(
        {"codex": ZeroShellProvider()},
        DummyRouter("codex", "gpt-6-luna"),
        store=store,
        workflow_id=wid,
    )
    prompt = load_prompt("constitution_validator", feature="provider-summary", artifact=const_file.read_text())
    result = runner.run("constitution_validator", prompt, cwd=tmp_path)
    assert result.success is True
    parsed = parse_validation(result.stdout, "constitution_validator", result.model)
    assert parsed.status == "PASS"
    assert parsed.summary == "Valid constitution"
    assert parsed.issues == []


def test_blocked_semantic_persisted_as_blocked_not_parse_error(tmp_path):
    store, wid = _setup_store(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    raw_response = json.dumps({
        "status": "BLOCKED",
        "summary": "Feature conflicts with project core tenets",
        "issues": ["Core principle of immutability violated in section 4"],
    })
    parsed = parse_validation(raw_response, "constitution_validator", "gpt-6-luna")
    assert parsed.status == "BLOCKED"
    assert parsed.status != "PARSE_ERROR"

    agent_res = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout=raw_response)
    runner.record_validation(agent_res, parsed, stage="CONSTITUTION_VALIDATE")

    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 1
    assert results[0]["status"] == "BLOCKED"
    assert results[0]["status"] != "PARSE_ERROR"
    assert results[0]["reason"] == "Feature conflicts with project core tenets"
    assert results[0]["summary"] == "Feature conflicts with project core tenets"
    assert results[0]["issues"] == ["Core principle of immutability violated in section 4"]


def test_all_seven_validators_receive_minimal_corresponding_artifacts():
    prompts_to_check = {
        "constitution_validator": load_prompt("constitution_validator", feature="REQ", artifact="CONST_TEXT"),
        "specification_validator": load_prompt("specification_validator", feature="REQ", artifact="SPEC_TEXT"),
        "plan_validator": load_prompt("plan_validator", feature="REQ", artifact="PLAN_TEXT"),
        "tasks_validator": load_prompt("tasks_validator", feature="REQ", artifact="TASKS_TEXT"),
        "test_validator": load_prompt("test_validator", task="TASK_1", artifact="TEST_CODE"),
        "code_reviewer": load_prompt("code_reviewer", task="TASK_1", artifact="TDD_EVIDENCE"),
        "final_reviewer": load_prompt("final_reviewer", task="WHOLE_FEAT", artifact="VERIFY_RESULTS"),
    }

    for role, prompt_text in prompts_to_check.items():
        # All must instruct not to use shell/commands
        assert "Do not attempt to read files via shell or execute any commands." in prompt_text, f"Failed for {role}"
        assert "Analyze only the provided artifacts and context below." in prompt_text, f"Failed for {role}"
        # All must enforce canonical JSON contract
        assert '{"status":"PASS|REVISE|BLOCKED","summary":"...","issues":[]}' in prompt_text, f"Failed for {role}"
        # All must include their respective artifact
        if role == "constitution_validator":
            assert "CONSTITUTION TO VALIDATE:\nCONST_TEXT" in prompt_text
        elif role == "specification_validator":
            assert "SPECIFICATION TO VALIDATE:\nSPEC_TEXT" in prompt_text
        elif role == "plan_validator":
            assert "PLAN TO VALIDATE:\nPLAN_TEXT" in prompt_text
        elif role == "tasks_validator":
            assert "TASKS TO VALIDATE:\nTASKS_TEXT" in prompt_text
        elif role == "test_validator":
            assert "TESTS / ARTIFACT:\nTEST_CODE" in prompt_text
        elif role in ("code_reviewer", "final_reviewer"):
            assert "EVIDENCE / ARTIFACT:\n" in prompt_text


def test_no_validator_receives_write_access():
    validator_roles = [
        "constitution_validator",
        "specification_validator",
        "plan_validator",
        "tasks_validator",
        "test_validator",
        "code_reviewer",
        "final_reviewer",
    ]

    for role_name in validator_roles:
        role_spec = ROLES[role_name]
        assert role_spec.validation is True, f"{role_name} should have validation=True"
        assert role_spec.edits_files is False, f"{role_name} must NOT edit files"

    # Provider command flags: Codex
    codex = CodexProvider()
    codex._exec_help = "--sandbox --model --json --cd"
    for role_name in validator_roles:
        cmd = codex.build_command("prompt", role_name)
        assert "--sandbox" in cmd
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only", f"{role_name} in codex must be read-only sandbox"

    # Provider command flags: AGY
    agy = AgyProvider()
    for role_name in validator_roles:
        cmd = agy.build_command("prompt", role_name, permissions="read")
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "plan", f"{role_name} in agy must be plan (read-only) mode"


def test_interactive_gate_shows_artifacts_supplied_and_empty_files_allowed():
    gate = InteractiveGate(input_fn=lambda prompt: "continue")
    summary = gate._summary(
        stage="CONSTITUTION_VALIDATE",
        role="constitution_validator",
        provider="codex",
        model="gpt-6-luna",
        files=[],
        commands=[],
        task_id=None,
        artifacts=[".specify/memory/constitution.md"],
    )
    assert summary["files_allowed"] == []
    assert summary["artifacts_supplied"] == [".specify/memory/constitution.md"]


def test_call_gate_helper_forwards_artifacts():
    calls = []

    def mock_gate(stage, role, files=(), commands=(), attempt=1, artifacts=()):
        calls.append((stage, role, list(files), list(artifacts)))
        return True

    _call_gate(mock_gate, "CONSTITUTION_VALIDATE", "constitution_validator", [], [], artifacts=[".specify/memory/constitution.md"])
    assert len(calls) == 1
    assert calls[0] == ("CONSTITUTION_VALIDATE", "constitution_validator", [], [".specify/memory/constitution.md"])


def test_no_context_includes_secrets_unduly(tmp_path):
    store, wid = _setup_store(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    raw = (
        '{"status": "PASS", "summary": "ok", '
        '"api_key": "api_key=SK_SECRET_KEY_1234567890", '
        '"bearer": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token", '
        '"issues": []}'
    )
    sanitized = sanitize_text(raw)
    assert "SK_SECRET_KEY_1234567890" not in sanitized
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in sanitized
    assert "[REDACTED]" in sanitized

    res = parse_validation(raw, "constitution_validator")
    agent_res = AgentResult("codex", "gpt-6-luna", "constitution_validator", True, stdout=raw)
    runner.record_validation(agent_res, res, stage="CONSTITUTION_VALIDATE")

    wf = store.get_workflow(wid)
    entry = wf["state"]["validation_results"][0]
    assert "SK_SECRET_KEY_1234567890" not in entry["raw_response"]
    assert "SK_SECRET_KEY_1234567890" not in entry["reason"]
    assert "[REDACTED]" in entry["raw_response"]
