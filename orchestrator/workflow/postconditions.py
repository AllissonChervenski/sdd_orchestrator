"""Deterministic filesystem gates for canonical SpecKit artifacts.

Provider success only describes execution. It never proves that a stage has
materialized its canonical artifact or permits a durable checkpoint by itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from orchestrator.workflow.artifact_content import artifact_content_problem
from orchestrator.workflow.constitution import classify_constitution, find_constitution_placeholders
from orchestrator.workflow.task_adapter import parse_speckit_tasks, TaskContractError
from orchestrator.workflow.quality_gates import clarification_questions, analyze_has_critical_findings


class PostconditionError(ValueError):
    """Raised when a stage postcondition is violated."""


def artifact_snapshot(path: Path | str) -> bytes | None:
    """Read artifact bytes before dispatch, keeping absence distinct from empty."""
    artifact = Path(path)
    if artifact.is_symlink():
        raise PostconditionError(f"Canonical artifact must not be a symlink: {artifact}")
    if not artifact.exists():
        return None
    if not artifact.is_file():
        raise PostconditionError(f"Canonical artifact is not a regular file: {artifact}")
    try:
        return artifact.read_bytes()
    except OSError as exc:
        raise PostconditionError(f"Cannot read canonical artifact at {artifact}: {exc}") from exc


def verify_artifact_changed(path: Path | str, before: bytes | None) -> None:
    """Require an author to create or change canonical bytes, not merely stdout."""
    after = artifact_snapshot(path)
    if after is None:
        raise PostconditionError(f"Canonical artifact missing after skill execution at {path}")
    if after == before:
        raise PostconditionError(f"Canonical artifact unchanged after skill execution at {path}")


def _read_artifact(path: Path | str, kind: str, *, require_heading: bool = True,
                   allow_clarification: bool = False) -> str:
    data = artifact_snapshot(path)
    if data is None:
        raise PostconditionError(f"{kind} artifact missing at {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise PostconditionError(f"{kind} artifact at {path} is not valid UTF-8") from exc
    problem = artifact_content_problem(
        text, require_heading=require_heading, allow_clarification=allow_clarification,
    )
    if problem:
        raise PostconditionError(f"{kind} artifact at {path} is invalid: {problem}")
    return text


def verify_constitution_created(constitution_path: Path | str) -> None:
    """Verify an actual constitution rather than an execution record or template."""
    text = _read_artifact(constitution_path, "Constitution")
    status = classify_constitution(text)
    if status != "VALID":
        placeholders = find_constitution_placeholders(text)
        detail = f"placeholders remaining: {placeholders}" if placeholders else f"classified as {status}"
        raise PostconditionError(f"Constitution is not valid: {detail}")


def verify_specification_valid(spec_path: Path | str) -> None:
    """Require authored Markdown; unresolved questions have their own stage gate."""
    _read_artifact(spec_path, "Specification", allow_clarification=True)


def verify_clarification_complete(spec_path: Path | str) -> None:
    """Verify specification exists and has no unresolved clarification markers."""
    text = _read_artifact(spec_path, "Specification", allow_clarification=True)
    questions = clarification_questions(text)
    if questions:
        raise PostconditionError(
            f"Specification at {spec_path} contains unresolved clarification markers: {questions}"
        )


def verify_checklist_complete(checklist_dir: Path | str, allow_legacy_absent: bool = False) -> None:
    """Verify every checklist is materialized; legacy checkpoints cannot bypass it."""
    dir_path = Path(checklist_dir)
    if not dir_path.is_dir() or dir_path.is_symlink():
        raise PostconditionError(f"Checklist directory missing or invalid at {dir_path}")
    md_files = sorted(dir_path.glob("*.md"))
    if not md_files:
        raise PostconditionError(f"No checklist markdown files found in {dir_path}")
    for path in md_files:
        text = _read_artifact(path, "Checklist")
        if not re.search(r"^\s*- \[[ xX]\]\s+\S", text, re.MULTILINE):
            raise PostconditionError(f"Checklist at {path} contains no requirements-quality criteria")


def verify_plan_valid(plan_path: Path | str) -> None:
    """Verify that the plan is authored Markdown rather than a template or envelope."""
    _read_artifact(plan_path, "Plan")


def verify_tasks_valid(tasks_path: Path | str) -> list[dict[str, Any]]:
    """Verify that tasks.md contains executable SpecKit task contracts."""
    path = Path(tasks_path)
    text = _read_artifact(path, "Tasks", require_heading=False)
    try:
        tasks = parse_speckit_tasks(text)
    except TaskContractError as exc:
        raise PostconditionError(f"tasks.md must contain valid SpecKit checklist tasks and harness metadata: {exc}") from exc
    if not tasks:
        raise PostconditionError("tasks.md must contain valid SpecKit checklist tasks and harness metadata")
    for task in tasks:
        if not task.get("id"):
            raise PostconditionError(f"Task missing required 'id' in {path}")
        if "dependencies" not in task:
            raise PostconditionError(f"Task {task.get('id')} missing 'dependencies' in {path}")
    return tasks


def verify_analysis_complete(report_path: Path | str, allow_legacy_absent: bool = False) -> None:
    """Require a filesystem analysis report with an explicit zero critical count."""
    text = _read_artifact(report_path, "Analysis report")
    # Normalize emphasis so both '**Critical Issues Count**: 0' and table rows
    # remain valid. A missing, negative, or inconsistent assessment fails closed.
    normalized = text.replace("**", "").replace("`", "")
    counts = re.findall(r"Critical Issues Count\s*[:|]\s*(\d+)\s*(?=\||$)", normalized,
                        re.IGNORECASE | re.MULTILINE)
    if not counts:
        raise PostconditionError(f"Analysis report at {report_path} lacks an explicit Critical Issues Count")
    if any(int(count) != 0 for count in counts) or analyze_has_critical_findings(normalized):
        raise PostconditionError(f"Analysis report at {report_path} contains critical cross-artifact findings")


def verify_stage_postcondition(stage: str, **kwargs: Any) -> bool:
    """Verify deterministic stage postcondition. Returns True or raises PostconditionError."""
    if stage in {"CONSTITUTION_CREATE", "CONSTITUTION_CREATED"}:
        verify_constitution_created(kwargs["constitution_path"])
    elif stage in {"SPECIFY", "SPEC_VALIDATED"}:
        verify_specification_valid(kwargs["spec_path"])
    elif stage == "CLARIFICATION_COMPLETE":
        verify_clarification_complete(kwargs["spec_path"])
    elif stage == "CHECKLIST_COMPLETE":
        verify_checklist_complete(kwargs["checklist_dir"])
    elif stage in {"PLAN", "PLAN_VALIDATED"}:
        verify_plan_valid(kwargs["plan_path"])
    elif stage in {"TASKS", "TASKS_VALIDATED"}:
        verify_tasks_valid(kwargs["tasks_path"])
    elif stage == "ANALYSIS_COMPLETE":
        verify_analysis_complete(kwargs["report_path"])
    return True
