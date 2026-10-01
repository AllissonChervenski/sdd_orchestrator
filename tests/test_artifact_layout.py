import json

import pytest

from orchestrator.workflow.artifacts import ArtifactDiscoveryError, ArtifactLayout
from orchestrator.workflow.resume import WorkspaceFingerprint, compare_fingerprints


def test_discovers_official_speckit_artifacts_and_separates_operational_reports(tmp_path):
    feature = tmp_path / "specs" / "003-queue"
    feature.mkdir(parents=True)
    (tmp_path / ".specify").mkdir()
    (tmp_path / ".specify" / "feature.json").write_text(
        json.dumps({"feature_directory": "specs/003-queue"})
    )
    layout = ArtifactLayout.discover(tmp_path, "ignored free-form request", ".orchestrator/runs/w1")

    assert layout.constitution == tmp_path / ".specify" / "memory" / "constitution.md"
    assert layout.feature_artifacts == (feature / "spec.md", feature / "plan.md", feature / "tasks.md")
    assert layout.reports == tmp_path / ".orchestrator" / "runs" / "w1"
    assert layout.stage_scope("PLAN") == ["specs/003-queue/plan.md"]
    assert layout.stage_scope("TASKS") == ["specs/003-queue/tasks.md"]


@pytest.mark.parametrize("feature_directory", ["../outside", "specs/../outside", "other/feature"])
def test_rejects_unsafe_or_nonstandard_persisted_feature_directory(tmp_path, feature_directory):
    (tmp_path / ".specify").mkdir()
    (tmp_path / ".specify" / "feature.json").write_text(
        json.dumps({"feature_directory": feature_directory})
    )
    with pytest.raises(ArtifactDiscoveryError):
        ArtifactLayout.discover(tmp_path)


def test_workspace_fingerprint_hashes_canonical_artifacts_not_run_copies(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.workflow.resume._git", lambda *_: "main")
    feature = tmp_path / "specs" / "feature"
    feature.mkdir(parents=True)
    memory = tmp_path / ".specify" / "memory"
    memory.mkdir(parents=True)
    (tmp_path / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": "specs/feature"}))
    for path in (memory / "constitution.md", feature / "spec.md", feature / "plan.md", feature / "tasks.md"):
        path.write_text(path.name)
    report = tmp_path / ".orchestrator" / "runs" / "w1" / "T001" / "tdd.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}")

    fingerprint = WorkspaceFingerprint(tmp_path).capture("w1", "T001")
    names = set(fingerprint["artifact_hashes"])
    assert {".specify/memory/constitution.md", "specs/feature/spec.md", "specs/feature/plan.md", "specs/feature/tasks.md"} <= names
    assert ".orchestrator/runs/w1/spec.md" not in names
    report.write_text('{"changed": true}')
    current = WorkspaceFingerprint(tmp_path).capture("w1", "T001")
    assert compare_fingerprints(fingerprint, current)[0] == "UNSAFE_DIVERGENCE"
