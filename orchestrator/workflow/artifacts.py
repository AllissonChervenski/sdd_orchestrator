"""Locate SpecKit artifacts without creating a second artifact tree.

SpecKit owns the feature-directory selection.  Its persisted
``.specify/feature.json`` is therefore preferred over any name supplied by
the harness.  A simple feature-name fallback exists only for old harness
callers that pre-date that persisted context.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_FEATURE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class ArtifactDiscoveryError(ValueError):
    """The workspace does not identify a safe SpecKit feature directory."""


def _under_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ArtifactDiscoveryError("SpecKit feature directory escapes the workspace")
    return resolved


def _feature_directory(root: Path) -> Path | None:
    metadata = root / ".specify" / "feature.json"
    if not metadata.is_file():
        return None
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactDiscoveryError("Invalid .specify/feature.json") from exc
    value = payload.get("feature_directory") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ArtifactDiscoveryError(".specify/feature.json has no feature_directory")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ArtifactDiscoveryError("Unsafe SpecKit feature directory")
    resolved = _under_root(root, root / path)
    specs = _under_root(root, root / "specs")
    if resolved.parent != specs:
        raise ArtifactDiscoveryError("SpecKit feature directory must be directly under specs/")
    return resolved


@dataclass(frozen=True)
class ArtifactLayout:
    """Primary SDD artifacts plus separate, operational workflow reports."""

    root: Path
    feature_dir: Path | None
    workflow_dir: Path | None = None

    @classmethod
    def discover(cls, root: str | Path, feature: str | None = None,
                 workflow_dir: str | Path | None = None) -> "ArtifactLayout":
        workspace = Path(root).resolve()
        feature_dir = _feature_directory(workspace)
        # Compatibility for callers that already provide the resolved directory
        # basename.  Never derive a directory from a natural-language request.
        if feature_dir is None and isinstance(feature, str) and _FEATURE_NAME.fullmatch(feature):
            feature_dir = _under_root(workspace, workspace / "specs" / feature)
        reports = _under_root(workspace, Path(workflow_dir)) if workflow_dir and Path(workflow_dir).is_absolute() else (
            workspace / Path(workflow_dir) if workflow_dir else None
        )
        if reports is not None:
            reports = _under_root(workspace, reports)
        return cls(workspace, feature_dir, reports)

    @property
    def constitution(self) -> Path:
        return self.root / ".specify" / "memory" / "constitution.md"

    @property
    def constitution_status(self) -> str:
        from orchestrator.workflow.constitution import classify_constitution
        return classify_constitution(self.constitution)

    @property
    def constitution_is_uninitialized(self) -> bool:
        from orchestrator.workflow.constitution import is_uninitialized_constitution
        return is_uninitialized_constitution(self.constitution)

    def require_feature_dir(self) -> Path:
        if self.feature_dir is None:
            raise ArtifactDiscoveryError(
                "SpecKit feature directory is unresolved; run speckit-specify so it writes .specify/feature.json"
            )
        return self.feature_dir

    @property
    def spec(self) -> Path:
        return self.require_feature_dir() / "spec.md"

    @property
    def plan(self) -> Path:
        return self.require_feature_dir() / "plan.md"

    @property
    def tasks(self) -> Path:
        return self.require_feature_dir() / "tasks.md"

    @property
    def feature_artifacts(self) -> tuple[Path, ...]:
        return (self.spec, self.plan, self.tasks)

    @property
    def reports(self) -> Path:
        if self.workflow_dir is None:
            raise ArtifactDiscoveryError("Workflow report directory is required")
        return self.workflow_dir

    def stage_scope(self, stage: str) -> list[str]:
        """Narrow write scope required by the installed SpecKit stage."""
        if stage == "CONSTITUTION":
            return [".specify/memory/constitution.md"]
        if stage == "SPECIFICATION":
            if self.feature_dir is not None:
                return [self.spec.relative_to(self.root).as_posix()]
            # The skill selects one direct child of specs/.  This glob permits
            # only spec.md, never unrelated files or nested feature directories.
            return [".specify/feature.json", "specs/*/spec.md"]
        if stage == "ANALYSIS":
            return [(self.reports / "analysis-report.md").relative_to(self.root).as_posix()]
        feature_dir = self.require_feature_dir().relative_to(self.root).as_posix()
        if stage == "CLARIFICATION":
            return [f"{feature_dir}/spec.md"]
        if stage == "REQUIREMENTS_CHECKLIST":
            return [f"{feature_dir}/checklists/"]
        if stage == "PLAN":
            return [f"{feature_dir}/plan.md"]
        if stage == "TASKS":
            return [f"{feature_dir}/tasks.md"]
        if stage == "CONVERGENCE":
            return [f"{feature_dir}/tasks.md"]
        return []

    def fingerprint_paths(self, task_id: str | None = None) -> list[str]:
        paths = [str(self.constitution)]
        metadata = self.root / ".specify" / "feature.json"
        if metadata.is_file():
            paths.append(str(metadata))
        if self.feature_dir is not None:
            paths.extend(str(path) for path in self.feature_artifacts)
            paths.extend(str(path) for path in sorted((self.feature_dir / "checklists").glob("*.md")))
        if self.workflow_dir is not None:
            analysis = self.workflow_dir / "analysis-report.md"
            if analysis.is_file():
                paths.append(str(analysis))
            paths.extend(str(path) for path in sorted(self.workflow_dir.glob("convergence-report-*.json")))
        if task_id and self.workflow_dir is not None:
            paths.append(str(self.workflow_dir / task_id / "tdd.json"))
        return paths
