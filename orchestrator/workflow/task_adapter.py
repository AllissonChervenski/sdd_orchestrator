"""Read SpecKit checklist tasks into the harness contract without rewriting tasks.md."""

import json
import re
from pathlib import PurePosixPath


_TASK = re.compile(r"^- \[[ xX]\] (T\d{3,})\b(?: \[P\])?(?: \[US\d+\])? (.+)$")
_METADATA = re.compile(r"^\s*<!-- harness-task (\{.*\}) -->\s*$")
_PHASES = ["RED", "GREEN", "REFACTOR"]
_FIELDS = ("requirements", "acceptance_criteria", "plan_decisions", "dependencies", "allowed_files")


class TaskContractError(ValueError):
    """A generated task cannot be safely consumed by the TDD harness."""


def _strings(value: object, field: str, task_id: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise TaskContractError(f"{task_id}: {field} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise TaskContractError(f"{task_id}: duplicate values in {field}")
    return value


def _check_paths(paths: list[str], task_id: str) -> None:
    for value in paths:
        path = PurePosixPath(value)
        if (path.is_absolute() or "\\" in value or ".." in path.parts or
                value.startswith("-") or not path.parts or path.parts == (".",) or
                "tests" in path.parts or path.name.startswith("test_")):
            raise TaskContractError(f"{task_id}: unsafe allowed_files path {value!r}")


def _contract(task_id: str, description: str, metadata: object) -> dict:
    if not isinstance(metadata, dict):
        raise TaskContractError(f"{task_id}: harness metadata must be a JSON object")
    for field in _FIELDS:
        if field not in metadata:
            raise TaskContractError(f"{task_id}: missing {field}")
        _strings(metadata[field], field, task_id)
    _check_paths(metadata["allowed_files"], task_id)
    test_type = metadata.get("test_type")
    if not isinstance(test_type, str) or test_type not in {"UNIT", "INTEGRATION", "CONTRACT", "E2E", "NOT_AUTOMATABLE"}:
        raise TaskContractError(f"{task_id}: invalid test_type")
    if test_type == "NOT_AUTOMATABLE":
        if not metadata.get("justification") or not metadata.get("alternative_verification"):
            raise TaskContractError(f"{task_id}: non-automatable task needs justification and alternative_verification")
    elif metadata.get("tdd_phases") != _PHASES:
        raise TaskContractError(f"{task_id}: tdd_phases must be RED, GREEN, REFACTOR")
    if not metadata["requirements"] or not metadata["acceptance_criteria"]:
        raise TaskContractError(f"{task_id}: missing requirement or acceptance criterion links")
    if not metadata["allowed_files"] and test_type != "NOT_AUTOMATABLE":
        raise TaskContractError(f"{task_id}: allowed_files must name production scope")
    numeric_sensitive = metadata.get("numeric_sensitive", False)
    if not isinstance(numeric_sensitive, bool):
        raise TaskContractError(f"{task_id}: numeric_sensitive must be a boolean")
    if not numeric_sensitive:
        text_corpus = " ".join([description] + metadata.get("requirements", []) + metadata.get("acceptance_criteria", []))
        forbidden_match = re.search(r"\b(filter|dsp|rms|envelope|sampling|emg)\b", text_corpus, re.IGNORECASE)
        if forbidden_match:
            raise TaskContractError(
                f"MISCLASSIFIED_TASK: task {task_id} contains numerical/DSP keyword '{forbidden_match.group(1)}' "
                f"but numeric_sensitive is false"
            )
    return {**metadata, "numeric_sensitive": numeric_sensitive, "id": task_id, "description": description}


def parse_speckit_tasks(text: str) -> list[dict]:
    """Parse checklist plus adjacent metadata; accept old JSON artifacts for resume."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except ValueError as exc:
            raise TaskContractError("Invalid legacy JSON tasks artifact") from exc
        legacy_tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(legacy_tasks, list) or not legacy_tasks or any(not isinstance(t, dict) or not t.get("id") for t in legacy_tasks):
            raise TaskContractError("Legacy JSON artifact has no tasks array")
        return legacy_tasks

    tasks: list[dict] = []
    seen: set[str] = set()
    lines = text.splitlines()
    fenced = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            fenced = not fenced
            index += 1
            continue
        match = None if fenced else _TASK.fullmatch(line)
        if match:
            task_id, description = match.groups()
            if task_id in seen:
                raise TaskContractError(f"Duplicate task ID {task_id}")
            if index + 1 >= len(lines) or not (meta := _METADATA.fullmatch(lines[index + 1])):
                raise TaskContractError(f"{task_id}: missing adjacent harness-task metadata")
            try:
                data = json.loads(meta.group(1))
            except ValueError as exc:
                raise TaskContractError(f"{task_id}: invalid harness-task JSON") from exc
            task = _contract(task_id, description, data)
            unknown = set(task["dependencies"]) - seen
            if unknown:
                raise TaskContractError(f"{task_id}: dependencies must reference earlier tasks: {sorted(unknown)}")
            tasks.append(task)
            seen.add(task_id)
            index += 2
            continue
        if not fenced and _METADATA.fullmatch(line):
            raise TaskContractError("Orphan harness-task metadata")
        if not fenced and re.match(r"^- \[[ xX]\]", line):
            raise TaskContractError(f"Malformed SpecKit checklist task on line {index + 1}")
        index += 1
    if not tasks:
        raise TaskContractError("SpecKit tasks.md has no executable checklist tasks")
    return tasks
