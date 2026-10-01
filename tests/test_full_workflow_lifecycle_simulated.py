import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.config.models import AgentResult, Config, ProviderCapabilities
from orchestrator.agents.router import ModelRouter
from orchestrator.agents.runner import AgentRunner
from orchestrator.storage.sqlite import StateStore
from orchestrator.verification.harness import RequirementVerification, VerificationResult
from orchestrator.workflow.driver import WorkflowBlocked, run_sdd_workflow


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.name", "Test Engineer"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "engineer@test.invalid"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "Initial commit"], cwd=tmp_path, check=True)
    return tmp_path


def _setup_env(tmp_path: Path, feature: str = "provider-summary"):
    root = _init_repo(tmp_path)
    store_dir = root / ".orchestrator" / "state"
    store_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(store_dir / "state.sqlite3")
    wid = store.create_workflow(
        feature,
        {
            "stage": "CONSTITUTION",
            "workspace": str(root),
            "first_real_run": False,
            "attempts": {},
            "completed_tasks": [],
            "blocked_tasks": [],
            "validation_results": [],
            "verification_results": [],
            "state_schema_version": store.SCHEMA_VERSION,
        },
    )
    workflow_dir = root / ".orchestrator" / "runs" / wid
    workflow_dir.mkdir(parents=True, exist_ok=True)
    return root, store, wid, workflow_dir


def _make_config() -> Config:
    cfg = Config()
    cfg.verification["regression_tests"] = [["python", "-m", "pytest", "-q"]]
    cfg.verification["tests"] = [["python", "-m", "pytest", "-q"]]
    return cfg


CONSTITUTION_CONTENT = """# Living Constitution

## Principle 1: Deterministic Control Plane
Python is the sole orchestrator. External workers cannot bypass deterministic gates.

## Principle 2: Strict Scope Containment
Workers only modify declared files.
"""

SPEC_CONTENT = """# Feature Specification: provider-summary

## FR-001 — Provider Summary
System shall list known providers.

## AC-001 — Valid Provider List
When executed, output displays provider names.
"""

CHECKLIST_CONTENT = """# Requirements Checklist: provider-summary
- [x] Clear requirements
- [x] Measurable acceptance criteria
"""

PLAN_CONTENT = """# Technical Implementation Plan: provider-summary
Architecture uses existing provider discovery.
"""

TASKS_CONTENT = """# Tasks

- [ ] T001 Implement provider summary command
  <!-- harness-task {"requirements":["FR-001"],"acceptance_criteria":["AC-001"],"plan_decisions":["D-001"],"dependencies":[],"test_type":"UNIT","allowed_files":["src/summary.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->
"""

AGY_SUCCESS_ENVELOPE = json.dumps({
    "conversation_id": "fake-agy-filesystem-regression",
    "status": "SUCCESS",
    "response": "",
    "duration_seconds": 1.0,
    "usage": {"input_tokens": 100, "output_tokens": 12},
})

ANALYSIS_CONTENT = "# SpecKit Analysis Report\nNo critical defects found.\nCritical Issues Count: 0\n"


class SimulatedRunner:
    def __init__(self, root: Path, store: StateStore, wid: str):
        self.root = root
        self.store = store
        self.workflow_id = wid
        self.calls: list[tuple[str, str, dict]] = []
        self.roles_called: list[str] = []
        self.skills_called: list[tuple[str, str]] = []
        self.validation_results: list[dict] = []
        self.tamper_in_green = False
        self.specification_skip_file = False
        self.constitution_placeholders = False
        self.validator_override: dict[str, str] = {}
        self.converge_action = "converge"  # "converge" or "append_once"
        self.appended = False
        self.artifact_stdout = AGY_SUCCESS_ENVELOPE
        self.needs_clarification = False

    def artifact_result(self, role):
        return AgentResult("agy", "gemini-3.8-flash-medium", role, True, stdout=self.artifact_stdout)

    def run_skill(self, role, provider, model, skill_name, prompt, cwd, **kwargs):
        self.skills_called.append((role, skill_name))
        return self.run(role, prompt, cwd=cwd, **kwargs)

    def run(self, role: str, prompt: str, **kwargs):
        self.calls.append((role, prompt, kwargs))
        self.roles_called.append(role)

        if role == "constitution":
            if self.constitution_placeholders:
                content = "# Living Constitution\n\n## Principle 1: [PRINCIPLE_1_NAME]\n[PRINCIPLE_1_DESCRIPTION]\n"
            else:
                content = CONSTITUTION_CONTENT
            const_file = self.root / ".specify" / "memory" / "constitution.md"
            const_file.parent.mkdir(parents=True, exist_ok=True)
            const_file.write_text(content, encoding="utf-8")
            return self.artifact_result(role)

        elif role == "specification":
            if self.specification_skip_file:
                # Provider reports success but produces no file and meaningless stdout
                return AgentResult("codex", "gpt-6-astra", role, True, stdout="")
            spec_file = self.root / "specs" / "provider-summary" / "spec.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            content = SPEC_CONTENT
            if self.needs_clarification:
                content += "\n[NEEDS CLARIFICATION: Which output format?]\n"
            revisions = self.roles_called.count("specification") - 1
            if revisions:
                content += f"\nRevision {revisions}: expanded error-condition requirements.\n"
            spec_file.write_text(content, encoding="utf-8")
            feature_json = self.root / ".specify" / "feature.json"
            feature_json.parent.mkdir(parents=True, exist_ok=True)
            feature_json.write_text(json.dumps({"feature_directory": "specs/provider-summary"}), encoding="utf-8")
            return self.artifact_result(role)

        elif role == "clarifier_agent":
            spec_file = self.root / "specs" / "provider-summary" / "spec.md"
            spec_file.write_text(spec_file.read_text().replace(
                "[NEEDS CLARIFICATION: Which output format?]", "Output format: JSON."
            ))
            return self.artifact_result(role)

        elif role == "requirements_reviewer":
            chk_file = self.root / "specs" / "provider-summary" / "checklists" / "requirements.md"
            chk_file.parent.mkdir(parents=True, exist_ok=True)
            chk_file.write_text(CHECKLIST_CONTENT, encoding="utf-8")
            return self.artifact_result(role)

        elif role == "planning":
            plan_file = self.root / "specs" / "provider-summary" / "plan.md"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(PLAN_CONTENT, encoding="utf-8")
            return self.artifact_result(role)

        elif role == "tasks":
            tasks_file = self.root / "specs" / "provider-summary" / "tasks.md"
            tasks_file.parent.mkdir(parents=True, exist_ok=True)
            tasks_file.write_text(TASKS_CONTENT, encoding="utf-8")
            return self.artifact_result(role)

        elif role == "consistency_agent":
            report = self.root / ".orchestrator" / "runs" / self.workflow_id / "analysis-report.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(ANALYSIS_CONTENT, encoding="utf-8")
            return self.artifact_result(role)

        elif role == "test_designer":
            if "ANALYZE only:" in prompt:
                return AgentResult("opencode", "tester", role, True, stdout="Approved test analysis")
            import re
            m = re.search(r"Task:\s*(T\d+)", prompt)
            task_id = m.group(1) if m else "T001"
            test_file = self.root / "tests" / f"test_{task_id}.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(f"def test_{task_id}():\n    assert False, 'RED expected failure'\n", encoding="utf-8")
            data = {
                "task_id": task_id,
                "requirement_ids": ["FR-001"],
                "acceptance_criteria_ids": ["AC-001"],
                "created_tests": [f"tests/test_{task_id}.py::test_{task_id}"],
                "test_commands": [["python", "-m", "pytest", "-q", f"tests/test_{task_id}.py::test_{task_id}"]],
            }
            return AgentResult("opencode", "tester", role, True, stdout=json.dumps(data))

        elif role == "coder":
            import re
            m = re.search(r"task\s+(T\d+)", prompt, re.IGNORECASE)
            task_id = m.group(1) if m else "T001"
            if self.tamper_in_green:
                # Illegal test tampering
                test_file = self.root / "tests" / f"test_{task_id}.py"
                test_file.write_text(f"def test_{task_id}():\n    assert True\n", encoding="utf-8")
            src_file = self.root / "src" / "summary.py"
            src_file.parent.mkdir(parents=True, exist_ok=True)
            src_file.write_text("VALUE = 'implemented'\n", encoding="utf-8")
            return AgentResult("opencode", "implementer", role, True, stdout=f"Implemented {task_id}")

        elif role == "refactorer":
            return AgentResult("opencode", "refactorer", role, True, stdout="Cleaned code")

        elif role == "convergence_agent":
            if self.converge_action == "append_once" and not self.appended:
                self.appended = True
                tasks_file = self.root / "specs" / "provider-summary" / "tasks.md"
                current = tasks_file.read_text(encoding="utf-8")
                residual = (
                    "- [ ] T002 Add json schema validation\n"
                    "  <!-- harness-task {\"requirements\":[\"FR-001\"],\"acceptance_criteria\":[\"AC-001\"],"
                    "\"plan_decisions\":[\"D-001\"],\"dependencies\":[\"T001\"],\"test_type\":\"UNIT\","
                    "\"allowed_files\":[\"src/summary.py\"],\"tdd_phases\":[\"RED\",\"GREEN\",\"REFACTOR\"]} -->\n"
                )
                tasks_file.write_text(current + "\n" + residual, encoding="utf-8")
                return self.artifact_result(role)
            envelope = json.loads(AGY_SUCCESS_ENVELOPE)
            envelope["response"] = "Converged — implementation satisfies spec, plan and tasks."
            return AgentResult("agy", "gemini-3.8-flash-medium", role, True, stdout=json.dumps(envelope))

        # Validators
        if role in self.validator_override:
            payload = self.validator_override[role]
            return AgentResult("agy", "gemini-flash", role, True, stdout=payload)

        # Default PASS for all validators
        pass_json = json.dumps({"status": "PASS", "summary": f"{role} passed", "issues": []})
        return AgentResult("agy", "gemini-flash", role, True, stdout=pass_json)

    def record_validation(self, agent_res, val_res, stage=None, evidence=None):
        entry = {
            "stage": stage or "VALIDATION",
            "role": agent_res.role,
            "provider": agent_res.provider,
            "model": agent_res.model,
            "status": val_res.status,
            "reason": val_res.summary,
            "summary": val_res.summary,
            "issues": list(val_res.issues),
            "evidence": evidence or {},
            "raw_response": getattr(val_res, "raw_output", "") or agent_res.stdout,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.validation_results.append(entry)
        if self.store and self.workflow_id:
            self.store.record_validation(self.workflow_id, entry)


class SimulatedHarness:
    def __init__(self, red_result: str = "EXPECTED_FAILURE", deterministic_success: bool = True):
        self.red_result = red_result
        self.deterministic_success = deterministic_success
        self.commands_run: list[tuple[str, list[str]]] = []
        self.requirement_results = [RequirementVerification("FR-001", status="PASS" if deterministic_success else "FAIL")]

    def run_red(self, command, **kwargs):
        self.commands_run.append(("RED", command))
        if self.red_result == "EXPECTED_FAILURE":
            return VerificationResult(command, False, 1, "FAILED assertion", "", 0.05, "EXPECTED_FAILURE", cause="assertion")
        elif self.red_result == "UNEXPECTED_PASS":
            return VerificationResult(command, True, 0, "PASSED", "", 0.05, "PASS")
        else:
            return VerificationResult(command, False, 2, "SyntaxError", "SyntaxError", 0.05, "SYNTAX_ERROR")

    def run_command(self, command, **kwargs):
        cat = kwargs.get("category", "command")
        self.commands_run.append((cat, command))
        return VerificationResult(command, True, 0, "OK", "", 0.05, "PASS")

    def run(self):
        self.commands_run.append(("HARNESS_RUN", ["pytest", "-q"]))
        return [
            VerificationResult(
                ["pytest", "-q"],
                self.deterministic_success,
                0 if self.deterministic_success else 1,
                "Suite output",
                "",
                0.1,
                "PASS" if self.deterministic_success else "FAIL",
                name="full test suite",
            )
        ]


# ==============================================================================
# 15 Test Scenarios (A through O)
# ==============================================================================


def test_scenario_a_happy_path_full_lifecycle(tmp_path):
    """Scenario A: Full happy path execution through all stages."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    harness = SimulatedHarness()
    cfg = _make_config()

    def approve():
        return True

    result = run_sdd_workflow(
        "provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=approve,
        store=store,
    )

    assert result["completed_tasks"] == ["T001"]
    assert result["final_verification"] == ["PASS"]

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "CONSTITUTION_CREATED" in checkpoints
    assert "CONSTITUTION_VALIDATED" in checkpoints
    assert "SPEC_VALIDATED" in checkpoints
    assert "CLARIFICATION_COMPLETE" in checkpoints
    assert "CHECKLIST_COMPLETE" in checkpoints
    assert "PLAN_VALIDATED" in checkpoints
    assert "TASKS_VALIDATED" in checkpoints
    assert "ANALYSIS_COMPLETE" in checkpoints
    assert "TASK_COMPLETE" in checkpoints
    assert "CONVERGED" in checkpoints
    assert "FINAL_VERIFIED" in checkpoints
    assert "FINAL_REVIEWED" in checkpoints
    assert checkpoints.index("CONVERGED") < checkpoints.index("FINAL_VERIFIED") < checkpoints.index("FINAL_REVIEWED")


def test_scenario_b_provider_success_without_artifact_blocks(tmp_path):
    """Scenario B: Provider returns SUCCESS but fails to produce expected file; postcondition blocks."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.specification_skip_file = True  # Provider outputs empty / no file
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "specification postcondition failure: Canonical artifact missing" in str(exc_info.value)

    # Deterministic checkpoint guarantee: SPEC_VALIDATED MUST NOT be created
    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "SPEC_VALIDATED" not in checkpoints


def test_scenario_c_incomplete_artifact_with_placeholders_blocks(tmp_path):
    """Scenario C: Provider produces artifact with unreplaced template placeholders."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.constitution_placeholders = True
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "Constitution postcondition failure" in str(exc_info.value)

    # Postcondition blocks durable checkpoint creation
    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "CONSTITUTION_CREATED" not in checkpoints
    assert "CONSTITUTION_VALIDATED" not in checkpoints


def test_scenario_d_validator_parse_error_preserves_evidence_and_blocks(tmp_path):
    """Scenario D: Validator returns unparseable schema; records PARSE_ERROR with evidence and blocks."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.validator_override["constitution_validator"] = "I am a validator and I think it is totally good!"
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "constitution_validator PARSE_ERROR" in str(exc_info.value)

    # Check evidence was preserved in sqlite store
    state = store.get_workflow(wid)["state"]
    val_results = state.get("validation_results", [])
    assert len(val_results) >= 1
    parse_err = next((v for v in val_results if v["stage"] == "CONSTITUTION_VALIDATE"), None)
    assert parse_err is not None
    assert parse_err["status"] == "PARSE_ERROR"
    assert "totally good" in parse_err["raw_response"]

    # No checkpoint created
    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "CONSTITUTION_VALIDATED" not in checkpoints


def test_scenario_e_validator_revise_with_issues_blocks_at_retry_limit(tmp_path):
    """Scenario E: Validator returns REVISE with issues; refinement exhausted and blocks."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    revise_payload = json.dumps({
        "status": "REVISE",
        "summary": "Missing requirement details",
        "issues": ["FR-001 needs error condition specification"],
    })
    runner.validator_override["specification_validator"] = revise_payload
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "specification_validator retry limit exceeded" in str(exc_info.value)

    state = store.get_workflow(wid)["state"]
    val_results = state.get("validation_results", [])
    spec_revises = [v for v in val_results if v["role"] == "specification_validator"]
    assert len(spec_revises) >= 1
    assert spec_revises[0]["status"] == "REVISE"
    assert spec_revises[0]["issues"] == ["FR-001 needs error condition specification"]

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "SPEC_VALIDATED" not in checkpoints


def test_scenario_f_validator_blocked_stops_workflow_and_persists_diagnostic(tmp_path):
    """Scenario F: Validator returns BLOCKED with issues; workflow stops immediately and records diagnostic."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    blocked_payload = json.dumps({
        "status": "BLOCKED",
        "summary": "Direct constitutional violation",
        "issues": ["Feature violates Principle 2 of the Constitution"],
    })
    runner.validator_override["specification_validator"] = blocked_payload
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "specification_validator BLOCKED" in str(exc_info.value)

    state = store.get_workflow(wid)["state"]
    val_results = state.get("validation_results", [])
    blocked = [v for v in val_results if v["role"] == "specification_validator"]
    assert len(blocked) == 1
    assert blocked[0]["status"] == "BLOCKED"
    assert blocked[0]["issues"] == ["Feature violates Principle 2 of the Constitution"]
    assert blocked[0]["reason"] == "Direct constitutional violation"

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "SPEC_VALIDATED" not in checkpoints


def test_scenario_g_provider_transient_failure_falls_back(tmp_path):
    """Scenario G: Provider fails with transient error; ModelRouter and AgentRunner fallback succeeds."""
    root = _init_repo(tmp_path)
    store = StateStore(root / ".orchestrator" / "state" / "state.sqlite3")
    wid = store.create_workflow("feature")

    class FailingProvider:
        def __init__(self):
            self.calls = 0

        def run(self, prompt, role, model, cwd, timeout, permissions):
            self.calls += 1
            return AgentResult("codex", model, role, False, exit_code=1, error="PROVIDER_FAILURE: Rate limit 429")

    class HealthyProvider:
        def __init__(self):
            self.calls = 0

        def run(self, prompt, role, model, cwd, timeout, permissions):
            self.calls += 1
            return AgentResult("agy", model, role, True, exit_code=0, stdout="Success from fallback provider")

    failing = FailingProvider()
    healthy = HealthyProvider()

    caps = {
        "codex": ProviderCapabilities("codex", cli_available=True, models=["gpt-6-astra"], supports_model_selection=True, supports_file_editing=True, supports_shell=True),
        "agy": ProviderCapabilities("agy", cli_available=True, models=["gemini-flash"], supports_model_selection=True, supports_file_editing=True, supports_shell=True),
    }
    router = ModelRouter(caps)
    runner = AgentRunner({"codex": failing, "agy": healthy}, router, store=store, workflow_id=wid)

    result = runner.run("specification", "Generate spec", override_provider="codex")

    assert result.success is True
    assert result.provider == "agy"
    assert result.stdout == "Success from fallback provider"
    assert failing.calls == 1
    assert healthy.calls == 1


def test_scenario_h_resume_skips_already_validated_stages(tmp_path):
    """Scenario H: Resume after valid checkpoint skips already completed stages."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    harness = SimulatedHarness()
    cfg = _make_config()

    # Run workflow to completion
    run_sdd_workflow(
        "provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=lambda: True,
        store=store,
    )

    initial_checkpoints = len(store.checkpoints(wid))

    # Now create a fresh runner that would fail if authors were invoked
    class GuardedResumeRunner(SimulatedRunner):
        def run(self, role, prompt, **kwargs):
            if role in {"constitution", "specification", "planning", "tasks"}:
                raise AssertionError(f"Resume should not re-invoke author role: {role}")
            return super().run(role, prompt, **kwargs)

    resume_runner = GuardedResumeRunner(root, store, wid)
    resume_harness = SimulatedHarness()

    resume_result = run_sdd_workflow(
        "provider-summary",
        root,
        resume_runner,
        resume_harness,
        cfg,
        workflow_dir,
        store=store,
        resume=True,
    )

    assert resume_result["completed_tasks"] == ["T001"]
    assert len(store.checkpoints(wid)) == initial_checkpoints


def test_scenario_i_corrupt_artifact_after_checkpoint_detected_on_resume(tmp_path):
    """Scenario I: Corrupt or deleted artifact detected on resume; refuses to proceed from corrupt checkpoint."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    harness = SimulatedHarness()
    cfg = _make_config()

    run_sdd_workflow(
        "provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=lambda: True,
        store=store,
    )

    # Intentionally delete the validated spec.md
    spec_file = root / "specs" / "provider-summary" / "spec.md"
    assert spec_file.is_file()
    spec_file.unlink()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            store=store,
            resume=True,
        )

    assert "Missing validated artifact" in str(exc_info.value) or "postcondition failure" in str(exc_info.value)


def test_scenario_j_convergence_appends_residual_task_and_executes_it(tmp_path):
    """Scenario J: speckit-converge appends residual task; TDD executes new task; next pass converges."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.converge_action = "append_once"  # Appends T002 on pass 1, converges on pass 2
    harness = SimulatedHarness()
    cfg = _make_config()

    result = run_sdd_workflow(
        "provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=lambda: True,
        store=store,
    )

    assert sorted(result["completed_tasks"]) == ["T001", "T002"]
    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "TASKS_APPENDED" in checkpoints
    assert "CONVERGED" in checkpoints
    assert "FINAL_VERIFIED" in checkpoints
    assert "FINAL_REVIEWED" in checkpoints
    assert checkpoints.index("CONVERGED") < checkpoints.index("FINAL_VERIFIED") < checkpoints.index("FINAL_REVIEWED")


def test_scenario_k_test_tampering_detected_during_green(tmp_path):
    """Scenario K: Test tampering detected during GREEN; TDD gate aborts task without completing."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.tamper_in_green = True
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "TDD task T001 blocked" in str(exc_info.value)

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "TASK_COMPLETE" not in checkpoints


def test_scenario_l_invalid_red_rejected_by_python_gate(tmp_path):
    """Scenario L: RED produces unexpected pass instead of EXPECTED_FAILURE; Python gate rejects it."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    harness = SimulatedHarness(red_result="UNEXPECTED_PASS")
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "TDD task T001 blocked in phase BLOCKED" in str(exc_info.value)

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "RED_VALIDATED" not in checkpoints
    assert "GREEN_VALIDATED" not in checkpoints
    assert "TASK_COMPLETE" not in checkpoints


def test_scenario_m_action_denied_handled_gracefully_as_blocked(tmp_path):
    """Scenario M: Prohibited tool/command execution returns BLOCKED; workflow persists diagnostic cleanly."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.validator_override["constitution_validator"] = json.dumps({
        "status": "BLOCKED",
        "summary": "Validação bloqueada; execução de comandos negada",
        "issues": ["RunCommand denied: cannot execute shell in read-only validation"],
    })
    harness = SimulatedHarness()
    cfg = _make_config()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "provider-summary",
            root,
            runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    assert "constitution_validator BLOCKED" in str(exc_info.value)

    val_results = store.get_workflow(wid)["state"].get("validation_results", [])
    assert len(val_results) >= 1
    assert val_results[0]["status"] == "BLOCKED"
    assert "RunCommand denied" in val_results[0]["issues"][0]


def test_scenario_n_crash_after_sqlite_persistence_resumes_cleanly(tmp_path):
    """Scenario N: Interruption / crash after SQLite persistence; resume recovers clean consistent state."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    harness = SimulatedHarness()
    cfg = _make_config()

    class CrashAfterPlanningRunner(SimulatedRunner):
        def run(self, role, prompt, **kwargs):
            if role == "tasks":
                raise RuntimeError("SIMULATED_ENGINE_CRASH")
            return super().run(role, prompt, **kwargs)

    crash_runner = CrashAfterPlanningRunner(root, store, wid)

    with pytest.raises(RuntimeError, match="SIMULATED_ENGINE_CRASH"):
        run_sdd_workflow(
            "provider-summary",
            root,
            crash_runner,
            harness,
            cfg,
            workflow_dir,
            approve_constitution=lambda: True,
            store=store,
        )

    checkpoints_before = [row["stage"] for row in store.checkpoints(wid)]
    assert "PLAN_VALIDATED" in checkpoints_before
    assert "TASKS_VALIDATED" not in checkpoints_before

    # Now resume cleanly with healthy runner
    clean_runner = SimulatedRunner(root, store, wid)
    result = run_sdd_workflow(
        "provider-summary",
        root,
        clean_runner,
        harness,
        cfg,
        workflow_dir,
        store=store,
        resume=True,
    )

    assert result["completed_tasks"] == ["T001"]
    checkpoints_after = [row["stage"] for row in store.checkpoints(wid)]
    assert "TASKS_VALIDATED" in checkpoints_after
    assert "FINAL_REVIEWED" in checkpoints_after


def test_scenario_o_multiple_validators_append_only_in_validation_results(tmp_path):
    """Scenario O: Multiple validators executed in sequence persist append-only without overwriting."""
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    harness = SimulatedHarness()
    cfg = _make_config()

    run_sdd_workflow(
        "provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=lambda: True,
        store=store,
    )

    state = store.get_workflow(wid)["state"]
    val_results = state.get("validation_results", [])

    # We expect at least constitution_validator, specification_validator,
    # plan_validator, tasks_validator, and final_reviewer
    roles_recorded = [v["role"] for v in val_results]
    assert "constitution_validator" in roles_recorded
    assert "specification_validator" in roles_recorded
    assert "plan_validator" in roles_recorded
    assert "tasks_validator" in roles_recorded
    assert "final_reviewer" in roles_recorded

    # Check append-only ordering and no overwriting
    assert len(val_results) >= 5
    for entry in val_results:
        assert "status" in entry
        assert "summary" in entry
        assert "issues" in entry
        assert isinstance(entry["issues"], list)


@pytest.mark.parametrize("stdout", [AGY_SUCCESS_ENVELOPE, "", "Worker diagnostics without artifact content"])
def test_full_lifecycle_uses_files_for_every_skill_and_real_clarification(tmp_path, stdout):
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    runner.artifact_stdout = stdout
    runner.needs_clarification = True
    result = run_sdd_workflow(
        "provider-summary", root, runner, SimulatedHarness(), _make_config(), workflow_dir,
        approve_constitution=lambda: True, store=store,
        clarification_callback=lambda questions: ["JSON" for _ in questions],
    )
    assert result["completed_tasks"] == ["T001"]
    assert result["final_verification"] == ["PASS"]
    assert runner.skills_called == [
        ("constitution", "speckit-constitution"),
        ("specification", "speckit-specify"),
        ("clarifier_agent", "speckit-clarify"),
        ("requirements_reviewer", "speckit-checklist"),
        ("planning", "speckit-plan"),
        ("tasks", "speckit-tasks"),
        ("consistency_agent", "speckit-analyze"),
        ("convergence_agent", "speckit-converge"),
    ]
    expected = {
        root / ".specify/memory/constitution.md": CONSTITUTION_CONTENT,
        root / "specs/provider-summary/spec.md": SPEC_CONTENT + "\nOutput format: JSON.\n",
        root / "specs/provider-summary/checklists/requirements.md": CHECKLIST_CONTENT,
        root / "specs/provider-summary/plan.md": PLAN_CONTENT,
        root / "specs/provider-summary/tasks.md": TASKS_CONTENT,
        workflow_dir / "analysis-report.md": ANALYSIS_CONTENT,
    }
    for path, content in expected.items():
        assert path.read_text() == content
    validator_content = {
        "constitution_validator": CONSTITUTION_CONTENT,
        "specification_validator": SPEC_CONTENT,
        "plan_validator": PLAN_CONTENT,
        "tasks_validator": TASKS_CONTENT,
    }
    for role, prompt, options in runner.calls:
        if role in validator_content:
            assert validator_content[role] in prompt
            assert "fake-agy-filesystem-regression" not in prompt
        if role.endswith("validator") or role in {"code_reviewer", "final_reviewer"}:
            assert options["allowed_paths"] == []
    analysis_call = next(call for call in runner.calls if call[0] == "consistency_agent")
    assert analysis_call[2]["allowed_paths"] == [str((workflow_dir / "analysis-report.md").relative_to(root))]
    report = (workflow_dir / "convergence-report-1.json").read_text()
    assert "fake-agy-filesystem-regression" not in report
    assert '"usage"' not in report


@pytest.mark.parametrize("artifact", [
    ".specify/memory/constitution.md", "specs/provider-summary/spec.md",
    "specs/provider-summary/checklists/requirements.md", "specs/provider-summary/plan.md",
    "specs/provider-summary/tasks.md", "analysis-report.md",
])
@pytest.mark.parametrize("corruption", [AGY_SUCCESS_ENVELOPE, "# [FEATURE NAME]\n\n[TODO]\n", ""])
def test_resume_rejects_corrupted_files_despite_completed_checkpoints(tmp_path, artifact, corruption):
    root, store, wid, workflow_dir = _setup_env(tmp_path)
    runner = SimulatedRunner(root, store, wid)
    cfg = _make_config()
    run_sdd_workflow(
        "provider-summary", root, runner, SimulatedHarness(), cfg, workflow_dir,
        approve_constitution=lambda: True, store=store,
    )
    path = workflow_dir / artifact if artifact == "analysis-report.md" else root / artifact
    path.write_text(corruption)
    checkpoints_before = len(store.checkpoints(wid))
    resumed = SimulatedRunner(root, store, wid)
    with pytest.raises(WorkflowBlocked):
        run_sdd_workflow(
            "provider-summary", root, resumed, SimulatedHarness(), cfg, workflow_dir,
            store=store, resume=True,
        )
    assert resumed.calls == []
    assert path.read_text() == corruption
    assert len(store.checkpoints(wid)) == checkpoints_before


@pytest.mark.parametrize("role,checkpoint", [
    ("clarifier_agent", "CLARIFICATION_COMPLETE"),
    ("requirements_reviewer", "CHECKLIST_COMPLETE"),
    ("consistency_agent", "ANALYSIS_COMPLETE"),
    ("convergence_agent", "CONVERGED"),
])
def test_successful_noop_quality_skills_never_checkpoint(tmp_path, role, checkpoint):
    root, store, wid, workflow_dir = _setup_env(tmp_path)

    class NoOpRunner(SimulatedRunner):
        def run(self, current_role, prompt, **kwargs):
            if current_role == role:
                return self.artifact_result(current_role)
            return super().run(current_role, prompt, **kwargs)

    runner = NoOpRunner(root, store, wid)
    runner.needs_clarification = role == "clarifier_agent"
    # A stale, valid artifact must not satisfy a newly executed quality skill.
    if role == "requirements_reviewer":
        path = root / "specs/provider-summary/checklists/requirements.md"
        path.parent.mkdir(parents=True)
        path.write_text(CHECKLIST_CONTENT)
    elif role == "consistency_agent":
        (workflow_dir / "analysis-report.md").write_text(ANALYSIS_CONTENT)
    with pytest.raises(WorkflowBlocked):
        run_sdd_workflow(
            "provider-summary", root, runner, SimulatedHarness(), _make_config(), workflow_dir,
            approve_constitution=lambda: True, store=store,
            clarification_callback=lambda questions: ["JSON" for _ in questions],
        )
    assert checkpoint not in [row["stage"] for row in store.checkpoints(wid)]
