"""Small control-plane parsers for SpecKit quality-stage outcomes.

These helpers recognize stage inputs and outputs; they do not implement the
clarify, checklist, analyze, or converge methodology owned by SpecKit skills.
"""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Callable
from pathlib import Path


_CLARIFICATION = re.compile(r"\[NEEDS CLARIFICATION(?::\s*(.*?))?\]", re.IGNORECASE)


def clarification_questions(spec_text: str) -> list[str]:
    """Return the unresolved questions explicitly marked in a SpecKit spec."""
    return [
        (match.group(1) or "").strip() or "Please clarify this requirement."
        for match in _CLARIFICATION.finditer(spec_text)
    ]


def analyze_has_critical_findings(report: str) -> bool:
    """Recognize critical findings in the installed analyze skill's report."""
    if re.search(r"Critical Issues Count\s*[:|]\s*[1-9]\d*", report, re.IGNORECASE):
        return True
    return bool(re.search(r"\|\s*CRITICAL\s*\|", report, re.IGNORECASE))


def convergence_outcome(before: bytes, after: bytes, report: str) -> str:
    """Check filesystem changes and interpret only an explicit outcome diagnostic.

    Provider envelopes are transport metadata, never a tasks artifact. Empty
    responses and a transport SUCCESS cannot establish semantic convergence.
    """
    if after != before:
        if not after.startswith(before):
            raise ValueError("speckit-converge changed existing tasks.md content; append-only contract violated")
        return "tasks_appended"
    try:
        payload = json.loads(report)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if payload.get("outcome") == "converged":
            return "converged"
        response = payload.get("response", "")
        report = response if isinstance(response, str) else ""
    elif report and ("item.completed" in report or "agent_message" in report):
        for line in report.splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get("type") == "item.completed":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "agent_message":
                        msg = item.get("text", "")
                        if isinstance(msg, str) and msg.strip():
                            report = msg
            except (ValueError, TypeError):
                continue
    if re.search(r"\b(?:not|never)(?:\s+\w+){0,2}\s+converged\b", report, re.IGNORECASE):
        raise ValueError("speckit-converge returned no append and no explicit converged result")
    if re.search(r"^\s*(?:[#*✅]\s*)*converged\b", report, re.IGNORECASE | re.MULTILINE):
        return "converged"
    raise ValueError("speckit-converge returned no append and no explicit converged result")


def run_convergence_loop(
    max_iterations: int,
    converge: Callable[[int], str],
    implement_remaining: Callable[[int], None],
) -> int:
    """Run semantic convergence in a bounded loop until stabilized.

    The initial implementation pass has already run through the harness's
    task-level TDD engine. ``implement_remaining`` is called only after
    converge appends work, so that same engine remains the sole task executor.
    Once convergence returns 'converged', the loop exits so final verification
    can run on the stabilized codebase.
    """
    if max_iterations < 1:
        raise ValueError("max_convergence_iterations must be at least 1")
    for iteration in range(1, max_iterations + 1):
        outcome = converge(iteration)
        if outcome == "converged":
            return iteration
        if outcome != "tasks_appended":
            raise ValueError(f"Unsupported convergence outcome: {outcome}")
        if iteration == max_iterations:
            raise ValueError(
                f"speckit-converge found remaining tasks after max_convergence_iterations={max_iterations}"
            )
        implement_remaining(iteration + 1)
    raise AssertionError("unreachable convergence loop exit")


def verify_convergence_receipt(path: Path, tasks_path: Path, expected_outcome: str) -> dict:
    """Revalidate Python's convergence receipt against the current tasks bytes."""
    try:
        if path.is_symlink() or tasks_path.is_symlink():
            raise ValueError("symlink evidence is not accepted")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("outcome") != expected_outcome:
            raise ValueError("missing or mismatched convergence outcome")
        before, after = receipt.get("tasks_sha256_before"), receipt.get("tasks_sha256_after")
        if not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in (before, after)):
            raise ValueError("missing task artifact hashes")
        if after != hashlib.sha256(tasks_path.read_bytes()).hexdigest():
            raise ValueError("tasks.md no longer matches the convergence receipt")
        added = receipt.get("added_task_ids")
        if not isinstance(added, list) or not all(isinstance(item, str) for item in added):
            raise ValueError("invalid appended task IDs")
        if expected_outcome == "converged" and (before != after or added):
            raise ValueError("converged receipt contradicts task changes")
        if expected_outcome == "tasks_appended" and (before == after or not added):
            raise ValueError("appended receipt has no new tasks")
        return receipt
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid convergence evidence at {path}: {exc}") from exc
