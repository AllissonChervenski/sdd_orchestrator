import json
import subprocess
from pathlib import Path

from orchestrator.config.models import AgentResult, Config, ProviderCapabilities
from orchestrator.agents.router import ModelRouter
from orchestrator.agents.runner import AgentRunner
from orchestrator.storage.sqlite import StateStore
from orchestrator.verification.harness import VerificationResult
from orchestrator.workflow.driver import run_sdd_workflow


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.name", "Test Engineer"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "engineer@test.invalid"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "Initial commit"], cwd=tmp_path, check=True)
    return tmp_path


CONSTITUTION_CONTENT = """# Living Constitution

## Principle 1: Deterministic Control Plane
Python is the sole orchestrator. External workers cannot bypass deterministic gates.

## Principle 6: Scientific and Numerical Oracle Integrity
Numerical code requires strict family separation.
"""

SPEC_CONTENT = """# Feature Specification: 001-provider-summary

## FR-001 — Provider Summary
System shall list known providers.

## AC-001 — Valid Provider List
When executed, output displays provider names.
"""

PLAN_CONTENT = """# Implementation Plan: 001-provider-summary

## User Review Required
No interactive review needed.

## Proposed Changes
Implement provider summary CLI.
"""

TASKS_CONTENT = """# Implementation Tasks: 001-provider-summary

- [ ] T001 Implement provider-summary CLI
<!-- harness-task {"requirements":["FR-001"],"acceptance_criteria":["AC-001"],"test_type":"UNIT","tdd_phases":["RED","GREEN","REFACTOR"],"plan_decisions":[],"dependencies":[],"allowed_files":["provider_summary.py"],"numeric_sensitive":false} -->
"""

TASK_CMD = ["python", "-m", "pytest", "-q", "tests/test_summary.py::test_summary"]
REG_CMD = ["python", "-m", "pytest", "-q"]
PASS_VAL = json.dumps({"status": "PASS", "issues": [], "summary": "Approved"})


def test_canary_execution_under_hardened_orchestrator(tmp_path):
    root = _init_repo(tmp_path)
    store_dir = root / ".orchestrator" / "state"
    store_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(store_dir / "orchestrator.sqlite3")

    # Copy real .agents skills directory into test workspace sandbox
    import shutil
    agents_src = Path(__file__).resolve().parents[1] / ".agents"
    if agents_src.is_dir():
        shutil.copytree(agents_src, root / ".agents")

    wid = store.create_workflow(
        "001-provider-summary",
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

    spec_dir = root / "specs" / "001-provider-summary"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "checklists").mkdir(parents=True, exist_ok=True)
    (spec_dir / "checklists" / "requirements.md").write_text("# Requirements Checklist\n- [ ] Item\n", encoding="utf-8")

    f_json = root / ".specify" / "feature.json"
    f_json.parent.mkdir(parents=True, exist_ok=True)
    f_json.write_text(json.dumps({"feature_directory": "specs/001-provider-summary"}), encoding="utf-8")

    class FakeWorkerProvider:
        def __init__(self, name, models):
            self.name = name
            self._models = list(models)
        def list_models(self):
            return list(self._models)
        def run(self, prompt, role, model=None, cwd=None, timeout=None, permissions=None):
            resolved_m = model or self._models[0]
            # Write artifact files when appropriate
            if role == "constitution":
                p = root / ".specify" / "memory" / "constitution.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(CONSTITUTION_CONTENT, encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="constitution generated")
            elif role == "specification":
                (spec_dir / "spec.md").write_text(SPEC_CONTENT, encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="spec generated")
            elif role == "requirements_reviewer":
                chk = spec_dir / "checklists" / "requirements.md"
                chk.write_text("# Requirements Checklist\n- [ ] Validated requirement items\n", encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="checklist updated")
            elif role == "consistency_agent":
                rep = workflow_dir / "analysis-report.md"
                rep.write_text("# Consistency Analysis Report\n\n**Critical Issues Count**: 0\n\nNo issues found.\n", encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="analysis complete")
            elif role == "planning":
                (spec_dir / "plan.md").write_text(PLAN_CONTENT, encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="plan generated")
            elif role == "tasks":
                (spec_dir / "tasks.md").write_text(TASKS_CONTENT, encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="tasks generated")
            elif role == "test_designer":
                if "ANALYZE" in prompt:
                    return AgentResult(self.name, resolved_m, role, True, stdout="analyzed")
                test_f = root / "tests" / "test_summary.py"
                test_f.parent.mkdir(parents=True, exist_ok=True)
                test_f.write_text("def test_summary(): assert False\n", encoding="utf-8")
                raw = json.dumps({
                    "task_id": "T001",
                    "requirement_ids": ["FR-001"],
                    "acceptance_criteria_ids": ["AC-001"],
                    "created_tests": ["tests/test_summary.py::test_summary"],
                    "test_commands": [TASK_CMD],
                })
                return AgentResult(self.name, resolved_m, role, True, stdout=raw)
            elif role == "coder":
                (root / "provider_summary.py").write_text("def summary(): return {}\n", encoding="utf-8")
                return AgentResult(self.name, resolved_m, role, True, stdout="implemented")
            elif role == "refactorer":
                return AgentResult(self.name, resolved_m, role, True, stdout="refactored")
            elif role == "convergence_agent":
                return AgentResult(self.name, resolved_m, role, True, stdout="✅ Converged — the implementation satisfies the spec, plan, and tasks.")
            elif role in ("constitution_validator", "specification_validator", "plan_validator", "tasks_validator", "test_validator", "code_reviewer", "final_reviewer"):
                return AgentResult(self.name, resolved_m, role, True, stdout=PASS_VAL)
            return AgentResult(self.name, resolved_m, role, True, stdout="done")
        def run_skill(self, skill, arguments, role, model=None, cwd=None, timeout=None, permissions=None, policy_prefix=""):
            return self.run(arguments, role, model=model, cwd=cwd, timeout=timeout, permissions=permissions)

    capabilities = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol", "gpt-6-astra"], True, True, True, True, False, True, False),
        "agy": ProviderCapabilities("agy", True, "1.0", ["claude-sonnet-4-6", "gemini-3.8-flash-high"], True, True, True, True, False, True, False),
        "opencode": ProviderCapabilities("opencode", True, "1.0", ["opencode-go/mimo-v2.6-pro", "opencode-go/kimi-k3"], True, True, True, True, False, True, False),
    }
    providers = {
        "codex": FakeWorkerProvider("codex", ["gpt-6-sol", "gpt-6-astra"]),
        "agy": FakeWorkerProvider("agy", ["claude-sonnet-4-6", "gemini-3.8-flash-high"]),
        "opencode": FakeWorkerProvider("opencode", ["opencode-go/mimo-v2.6-pro", "opencode-go/kimi-k3"]),
    }

    router = ModelRouter(capabilities)
    runner = AgentRunner(providers, router, store=store, workflow_id=wid)

    class Harness:
        def __init__(self, workspace):
            self.workspace = Path(workspace)
        def run_red(self, command, **kwargs):
            return VerificationResult(command, False, 1, "collected 1 item; FAILED tests/test_summary.py::test_summary - AssertionError", "", 0.1, "EXPECTED_FAILURE", cause="linked assertion")
        def run_command(self, command, **kwargs):
            return VerificationResult(command, True, 0, "", "", 0.1, "PASS")
        def run(self):
            return [VerificationResult(REG_CMD, True, 0, "", "", 0.1, "PASS")]
        @property
        def requirement_results(self):
            return []

    harness = Harness(root)

    cfg = Config()
    cfg.verification["regression_tests"] = [REG_CMD]
    cfg.verification["tests"] = [REG_CMD]
    cfg.human_gates["constitution_change"] = False

    # Execute workflow under hardened orchestrator
    state = run_sdd_workflow(
        "001-provider-summary",
        root,
        runner,
        harness,
        cfg,
        workflow_dir,
        approve_constitution=lambda: True,
        store=store,
    )

    # 1. Status final: PASS completo
    wf = store.get_workflow(wid)
    assert wf["stage"] == "FINAL_REVIEWED"
    assert state["final_verification"] == ["PASS"]
    assert state["completed_tasks"] == ["T001"]

    # 2. Telemetria gravada: provider_executions contém requested_model, resolved_model, resolution_source
    import sqlite3
    conn = sqlite3.connect(store_dir / "orchestrator.sqlite3")
    conn.row_factory = sqlite3.Row
    execs = conn.execute("SELECT * FROM provider_executions WHERE workflow_id = ?", (wid,)).fetchall()
    assert len(execs) > 0
    for e in execs:
        assert e["requested_model"] is not None
        assert e["resolved_model"] is not None
        assert e["resolution_source"] in ("provider_output", "adapter_explicit", "provider_metadata", "cli_default")

    # 3. Tasks table gravou numeric_sensitive
    tasks_db = conn.execute("SELECT * FROM tasks WHERE workflow_id = ?", (wid,)).fetchall()
    assert len(tasks_db) >= 1
    for t in tasks_db:
        assert t["numeric_sensitive"] in (0, 1, False, True)

    # 4. Checkpoints contêm fingerprint estendido com fixture_hashes
    checkpoints = store.checkpoints(wid)
    assert len(checkpoints) >= 5
    for cp in checkpoints:
        fp = cp["workspace_fingerprint"]
        assert "fixture_hashes" in fp
        assert isinstance(fp["fixture_hashes"], dict)
        if "resolved_models" in fp:
            assert isinstance(fp["resolved_models"], dict)

    cp_cols = conn.execute("SELECT resolved_models_json FROM checkpoints WHERE workflow_id = ?", (wid,)).fetchall()
    assert len(cp_cols) >= 5

    # 5. Final review and convergence artifacts exist
    assert (workflow_dir / "final-review.json").is_file()
    assert (workflow_dir / "convergence-report-1.json").is_file()
