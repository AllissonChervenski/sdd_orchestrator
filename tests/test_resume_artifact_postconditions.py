"""Saved hashes cannot make provider metadata into valid resumed artifacts."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from orchestrator.workflow.resume import WorkspaceFingerprint, revalidate_checkpoint


ENVELOPE = json.dumps({"conversation_id": "fake", "status": "SUCCESS", "response": "", "usage": {}})


class Harness:
    commands = {"regression_tests": [["python", "-m", "pytest", "-q"]]}
    requirement_results = []

    def __init__(self):
        self.calls = []

    def run_command(self, command, *args, **kwargs):
        self.calls.append(command)
        return SimpleNamespace(success=True, status="PASS")

    def run(self):
        self.calls.append("final")
        return [SimpleNamespace(success=True, status="PASS")]


def workspace(tmp_path):
    feature = tmp_path / "specs" / "feature"
    reports = tmp_path / ".orchestrator" / "runs" / "fake"
    memory = tmp_path / ".specify" / "memory"
    feature.mkdir(parents=True)
    reports.mkdir(parents=True)
    memory.mkdir(parents=True)
    (memory.parent / "feature.json").write_text(json.dumps({"feature_directory": "specs/feature"}))
    paths = {
        "constitution": memory / "constitution.md",
        "spec": feature / "spec.md",
        "plan": feature / "plan.md",
        "tasks": feature / "tasks.md",
        "checklist": feature / "checklists" / "requirements.md",
        "analysis": reports / "analysis-report.md",
    }
    paths["checklist"].parent.mkdir()
    paths["constitution"].write_text("# Constitution\n\nPython governs checkpoints.\n")
    paths["spec"].write_text("# Feature\n\nFR-001: Persist files. AC-001: Resume reads files.\n")
    paths["plan"].write_text("# Plan\n\nD-001: Use deterministic filesystem gates.\n")
    contract = {"requirements": ["FR-001"], "acceptance_criteria": ["AC-001"],
                "plan_decisions": ["D-001"], "dependencies": [], "allowed_files": ["src/feature.py"],
                "test_type": "UNIT", "tdd_phases": ["RED", "GREEN", "REFACTOR"]}
    paths["tasks"].write_text("# Tasks\n\n- [ ] T001 Implement in src/feature.py\n"
                              f"  <!-- harness-task {json.dumps(contract)} -->\n")
    paths["checklist"].write_text("# Requirements\n\n- [x] CHK001 Requirements have acceptance criteria.\n")
    paths["analysis"].write_text("# Analysis Report\n\nCritical Issues Count: 0\n")
    task_report = reports / "T001" / "tdd.json"
    task_report.parent.mkdir()
    task_report.write_text(json.dumps({"phase": "COMPLETE", "test_design": {
        "created_tests": ["tests/test_feature.py::test_feature"],
        "test_commands": [["pytest", "tests/test_feature.py::test_feature"]],
    }}))
    (reports / "final-review.json").write_text(json.dumps({"status": "PASS"}))
    digest = hashlib.sha256(paths["tasks"].read_bytes()).hexdigest()
    (reports / "convergence-report-1.json").write_text(json.dumps({
        "iteration": 1, "outcome": "converged", "added_task_ids": [],
        "tasks_sha256_before": digest, "tasks_sha256_after": digest,
    }))
    return paths, reports


def checkpoint(tmp_path, stage):
    return {"workflow_id": "fake", "stage": stage, "attempt": 1,
            "task_id": "T001" if stage == "TASK_COMPLETE" else None,
            "workspace_fingerprint": WorkspaceFingerprint(tmp_path).capture("fake")}


@pytest.mark.parametrize("stage,corrupt", [
    ("SPEC_VALIDATED", "constitution"),
    ("PLAN_VALIDATED", "constitution"),
    ("PLAN_VALIDATED", "spec"),
    ("TASKS_VALIDATED", "plan"),
    ("CROSS_VALIDATED", "constitution"),
    ("CROSS_VALIDATED", "spec"),
    ("CROSS_VALIDATED", "checklist"),
    ("CROSS_VALIDATED", "analysis"),
    ("ANALYSIS_COMPLETE", "tasks"),
    ("TASK_COMPLETE", "constitution"),
    ("TASK_COMPLETE", "spec"),
    ("TASK_COMPLETE", "tasks"),
    ("FINAL_VERIFIED", "plan"),
    ("FINAL_REVIEWED", "constitution"),
    ("FINAL_REVIEWED", "analysis"),
])
def test_resume_rejects_corruption_even_when_saved_hash_matches(tmp_path, stage, corrupt):
    paths, _ = workspace(tmp_path)
    paths[corrupt].write_text(ENVELOPE)
    saved = checkpoint(tmp_path, stage)
    harness = Harness()
    ok, detail = revalidate_checkpoint(saved, tmp_path, harness)
    assert not ok, detail
    assert "ARTIFACT_POSTCONDITION_FAILED" in detail
    assert harness.calls == []


def test_clarification_resume_rejects_unresolved_questions(tmp_path):
    paths, _ = workspace(tmp_path)
    paths["spec"].write_text("# Feature\n\nFR-001: [NEEDS CLARIFICATION: Which store?]\n")
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, "CLARIFICATION_COMPLETE"), tmp_path, Harness())
    assert not ok
    assert "clarification" in detail


@pytest.mark.parametrize("stage", ["CHECKLIST_COMPLETE", "PLAN_VALIDATED", "CROSS_VALIDATED", "ANALYSIS_COMPLETE"])
def test_resume_requires_checklist_dependency(tmp_path, stage):
    paths, _ = workspace(tmp_path)
    paths["checklist"].unlink()
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, stage), tmp_path, Harness())
    assert not ok, detail


@pytest.mark.parametrize("stage", ["CROSS_VALIDATED", "ANALYSIS_COMPLETE"])
def test_resume_requires_analysis_dependency(tmp_path, stage):
    paths, _ = workspace(tmp_path)
    paths["analysis"].unlink()
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, stage), tmp_path, Harness())
    assert not ok, detail


@pytest.mark.parametrize("stage", [
    "CONSTITUTION_CREATED", "CONSTITUTION_VALIDATED", "SPEC_VALIDATED", "CLARIFICATION_COMPLETE",
    "CHECKLIST_COMPLETE", "PLAN_VALIDATED", "TASKS_VALIDATED", "CROSS_VALIDATED",
    "ANALYSIS_COMPLETE", "TASK_COMPLETE", "CONVERGED", "FINAL_VERIFIED", "FINAL_REVIEWED",
])
def test_resume_valid_filesystem_dependencies_pass(tmp_path, stage):
    workspace(tmp_path)
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, stage), tmp_path, Harness())
    assert ok, detail


@pytest.mark.parametrize("change", ["missing", "metadata", "hash", "outcome", "iteration", "added_ids", "before_hash"])
def test_converged_resume_requires_authentic_receipt(tmp_path, change):
    paths, reports = workspace(tmp_path)
    receipt = reports / "convergence-report-1.json"
    data = json.loads(receipt.read_text())
    if change == "missing":
        receipt.unlink()
    elif change == "metadata":
        receipt.write_text(ENVELOPE)
    else:
        updates = {"hash": {"tasks_sha256_after": "0" * 64}, "outcome": {"outcome": "SUCCESS"},
                   "iteration": {"iteration": 2}, "added_ids": {"added_task_ids": ["T001"]},
                   "before_hash": {"tasks_sha256_before": "0" * 64}}
        data.update(updates[change])
        receipt.write_text(json.dumps(data))
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, "CONVERGED"), tmp_path, Harness())
    assert not ok, detail


def test_converged_resume_rejects_changed_tasks_even_if_new_checkpoint_hash_matches(tmp_path):
    paths, _ = workspace(tmp_path)
    paths["tasks"].write_text(paths["tasks"].read_text() + "\nChanged task rationale.\n")
    ok, detail = revalidate_checkpoint(checkpoint(tmp_path, "CONVERGED"), tmp_path, Harness())
    assert not ok, detail


def test_unknown_checkpoint_stage_fails_closed(tmp_path):
    workspace(tmp_path)
    assert revalidate_checkpoint(checkpoint(tmp_path, "PROVIDER_SUCCESS"), tmp_path, Harness())[0] is False
