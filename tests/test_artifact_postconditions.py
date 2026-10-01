"""Filesystem gates must reject execution metadata and unmaterialized documents."""

import json
from pathlib import Path

import pytest

from orchestrator.workflow.constitution import classify_constitution
from orchestrator.workflow.postconditions import (
    PostconditionError,
    artifact_snapshot,
    verify_analysis_complete,
    verify_artifact_changed,
    verify_checklist_complete,
    verify_clarification_complete,
    verify_constitution_created,
    verify_plan_valid,
    verify_specification_valid,
    verify_stage_postcondition,
    verify_tasks_valid,
)


ENVELOPE = json.dumps({
    "conversation_id": "fake-agy-conversation",
    "status": "SUCCESS",
    "response": "",
    "duration_seconds": 1.0,
    "usage": {"input_tokens": 17, "output_tokens": 0},
})
VERIFY_DOCUMENTS = [
    verify_constitution_created,
    verify_specification_valid,
    verify_plan_valid,
    verify_analysis_complete,
]


@pytest.mark.parametrize("verify", VERIFY_DOCUMENTS)
@pytest.mark.parametrize("content", [
    "", " \n\t", ENVELOPE, "```json\n" + ENVELOPE + "\n```",
    "# Generated document\n\n" + ENVELOPE,
    "# [FEATURE NAME]\n\n[Describe the feature]",
    "# Artifact\n\nTODO", "# Artifact\n", "worker completed successfully",
])
def test_document_gate_rejects_non_artifacts(tmp_path, verify, content):
    path = tmp_path / "artifact.md"
    path.write_text(content)
    with pytest.raises(PostconditionError):
        verify(path)
    assert path.read_text() == content


@pytest.mark.parametrize("content", [ENVELOPE, "# Constitution\n" + ENVELOPE, "# Constitution", "done"])
def test_constitution_classifier_rejects_corruption(content):
    assert classify_constitution(content) == "UNINITIALIZED_CONSTITUTION"


@pytest.mark.parametrize("verify,content", [
    (verify_constitution_created, "# Constitution\n\nPython governs stages and checkpoints.\n"),
    (verify_specification_valid, "# Feature\n\nFR-001: Persist artifacts.\nAC-001: Resume reads files.\n"),
    (verify_plan_valid, "# Implementation Plan\n\nUse the Python filesystem gate before checkpointing.\n"),
    (verify_analysis_complete, "# Specification Analysis Report\n\nCritical Issues Count: 0\n"),
])
def test_document_gate_accepts_materialized_markdown(tmp_path, verify, content):
    path = tmp_path / "artifact.md"
    path.write_text(content)
    verify(path)
    assert path.read_text() == content


def test_clarification_has_separate_resolution_gate(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# Feature\n\nFR-001: [NEEDS CLARIFICATION: Which storage?]\n")
    verify_specification_valid(spec)
    with pytest.raises(PostconditionError, match="unresolved clarification"):
        verify_clarification_complete(spec)


@pytest.mark.parametrize("verify,template", [
    (verify_constitution_created, "constitution-template.md"),
    (verify_specification_valid, "spec-template.md"),
    (verify_plan_valid, "plan-template.md"),
])
def test_installed_templates_are_not_artifacts(tmp_path, verify, template):
    source = Path(__file__).resolve().parents[1] / ".specify" / "templates" / template
    path = tmp_path / "artifact.md"
    path.write_bytes(source.read_bytes())
    with pytest.raises(PostconditionError):
        verify(path)


@pytest.mark.parametrize("content", [ENVELOPE, "# Checklist\n\nDone.", "# Checklist\n\n- [ ] CHK001 [Describe criterion]"])
def test_checklist_requires_real_criteria(tmp_path, content):
    folder = tmp_path / "checklists"
    folder.mkdir()
    (folder / "requirements.md").write_text(content)
    with pytest.raises(PostconditionError):
        verify_checklist_complete(folder)


def test_checklist_accepts_requirements_quality_items(tmp_path):
    folder = tmp_path / "checklists"
    folder.mkdir()
    (folder / "requirements.md").write_text("# Requirements Checklist\n\n- [x] CHK001 FR-001 has a measurable acceptance criterion.\n")
    verify_checklist_complete(folder)


@pytest.mark.parametrize("stage,path_key,path", [
    ("CHECKLIST_COMPLETE", "checklist_dir", "checklists"),
    ("ANALYSIS_COMPLETE", "report_path", "analysis-report.md"),
])
def test_legacy_checkpoint_cannot_bypass_absent_artifact(tmp_path, stage, path_key, path):
    with pytest.raises(PostconditionError):
        verify_stage_postcondition(stage, **{path_key: tmp_path / path}, allow_legacy_absent=True)


def test_snapshot_requires_real_content_change(tmp_path):
    path = tmp_path / "spec.md"
    assert artifact_snapshot(path) is None
    path.write_text("# Feature\n\nFR-001: Persist files.\n")
    verify_artifact_changed(path, None)
    before = artifact_snapshot(path)
    path.write_bytes(path.read_bytes())
    with pytest.raises(PostconditionError, match="unchanged"):
        verify_artifact_changed(path, before)
    path.write_text("# Feature\n\nFR-001: Persist and validate files.\n")
    verify_artifact_changed(path, before)


def test_snapshot_missing_artifact_is_deterministic(tmp_path):
    with pytest.raises(PostconditionError, match="missing"):
        verify_artifact_changed(tmp_path / "spec.md", None)


def test_tasks_gate_rejects_provider_envelope(tmp_path):
    path = tmp_path / "tasks.md"
    path.write_text(ENVELOPE)
    with pytest.raises(PostconditionError):
        verify_tasks_valid(path)


@pytest.mark.parametrize("verify", VERIFY_DOCUMENTS)
def test_invalid_utf8_is_deterministic(tmp_path, verify):
    path = tmp_path / "artifact.md"
    path.write_bytes(b"# Document\n\n\xff invalid utf8")
    with pytest.raises(PostconditionError):
        verify(path)


def test_analysis_requires_explicit_assessment(tmp_path):
    path = tmp_path / "analysis-report.md"
    path.write_text("# Analysis Report\n\nThe worker returned successfully.\n")
    with pytest.raises(PostconditionError):
        verify_analysis_complete(path)


def test_analysis_rejects_critical_findings(tmp_path):
    path = tmp_path / "analysis-report.md"
    path.write_text("# Analysis Report\n\nCritical Issues Count: 1\n")
    with pytest.raises(PostconditionError, match="critical"):
        verify_analysis_complete(path)
