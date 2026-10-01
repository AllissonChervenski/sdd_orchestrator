"""Deterministic detection and handling of SpecKit constitution templates and states."""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.workflow.artifact_content import artifact_content_problem


OFFICIAL_CONSTITUTION_PLACEHOLDERS: tuple[str, ...] = (
    "[PROJECT_NAME]",
    "[PRINCIPLE_1_NAME]",
    "[PRINCIPLE_1_DESCRIPTION]",
    "[PRINCIPLE_2_NAME]",
    "[PRINCIPLE_2_DESCRIPTION]",
    "[PRINCIPLE_3_NAME]",
    "[PRINCIPLE_3_DESCRIPTION]",
    "[PRINCIPLE_4_NAME]",
    "[PRINCIPLE_4_DESCRIPTION]",
    "[PRINCIPLE_5_NAME]",
    "[PRINCIPLE_5_DESCRIPTION]",
    "[SECTION_2_NAME]",
    "[SECTION_2_CONTENT]",
    "[SECTION_3_NAME]",
    "[SECTION_3_CONTENT]",
    "[GOVERNANCE_RULES]",
    "[GUIDANCE_FILE]",
    "[CONSTITUTION_VERSION]",
    "[RATIFICATION_DATE]",
    "[LAST_AMENDED_DATE]",
)

CRITICAL_CONSTITUTION_PLACEHOLDERS: tuple[str, ...] = (
    "[PROJECT_NAME]",
    "[PRINCIPLE_1_NAME]",
    "[PRINCIPLE_1_DESCRIPTION]",
    "[CONSTITUTION_VERSION]",
    "[RATIFICATION_DATE]",
)

_TEMPLATE_TOKEN_RE = re.compile(r"\[([A-Z][A-Z0-9_]{2,})\]")


def find_constitution_placeholders(content: str) -> list[str]:
    """Find template placeholders present in constitution content."""
    cleaned = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    found: list[str] = []
    for p in OFFICIAL_CONSTITUTION_PLACEHOLDERS:
        if p in cleaned and p not in found:
            found.append(p)
    for m in _TEMPLATE_TOKEN_RE.finditer(cleaned):
        token = m.group(0)
        end_idx = m.end()
        after = cleaned[end_idx : end_idx + 2]
        if after.startswith("(") or after.startswith(":"):
            # Exclude markdown links [NAME](url) or link references [NAME]: url
            continue
        if token not in found:
            found.append(token)
    return found


def classify_constitution(path_or_content: str | Path) -> str:
    """Deterministically classify a constitution artifact.

    Returns:
        - "ABSENT": File does not exist.
        - "UNINITIALIZED_CONSTITUTION": File is empty, template, or has placeholders.
        - "VALID": File is an actual, filled-in constitution.
    """
    if isinstance(path_or_content, Path):
        if not path_or_content.is_file():
            return "ABSENT"
        try:
            content = path_or_content.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "UNINITIALIZED_CONSTITUTION"
    else:
        content = path_or_content

    cleaned = content.strip()
    if artifact_content_problem(cleaned):
        return "UNINITIALIZED_CONSTITUTION"

    placeholders = find_constitution_placeholders(cleaned)
    if placeholders:
        return "UNINITIALIZED_CONSTITUTION"

    return "VALID"


def is_uninitialized_constitution(path_or_content: str | Path) -> bool:
    """Check if constitution is absent, empty, or an uninitialized template."""
    return classify_constitution(path_or_content) != "VALID"
