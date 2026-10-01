"""Shared, deterministic checks for materialized SpecKit document content."""

from __future__ import annotations

import json
import re


_HEADING = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_BRACKET = re.compile(r"\[([^\[\]\n]+)\](?![(:])")
_TEMPLATE_INSTRUCTION = re.compile(
    r"^(?:describe\b|explain\b|extract\b|document the\b|gates determined\b|brief\b|"
    r"add more\b|e\.g\.|if applicable\b|domain-specific\b|initial state$|action$|"
    r"expected outcome$|specific\b|key interaction\b|data requirement\b|behavior\b|"
    r"measurable\b|user satisfaction\b|business metric\b|boundary condition$|"
    r"error scenario$|what it represents\b|assumption\b|dependency on\b|"
    r"category \d+$|entity \d+$|link$|link to\b|###-|remove if unused\b|"
    r"same as\b|platform-specific\b|current need$|why \d)", re.IGNORECASE,
)


def artifact_content_problem(content: str, *, require_heading: bool = True,
                             allow_clarification: bool = False) -> str | None:
    """Return a structural defect, leaving semantic quality to independent review.

    JSON execution records, boilerplate, and title-only documents cannot prove a
    skill's filesystem postcondition. This check deliberately does not invent
    language-specific requirements or replace SpecKit's methodology.
    """
    text = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()
    if not text:
        return "empty content"
    if "\x00" in text or "\ufffd" in text:
        return "invalid text encoding"
    try:
        json.loads(text)
    except ValueError:
        pass
    else:
        return "JSON provider result is not a Markdown artifact"
    # Reject envelopes even when wrapped in Markdown headings or code fences.
    if (re.search(r'"(?:conversation_id|duration_seconds|denied_actions)"\s*:', text)
            or (re.search(r'"status"\s*:', text)
                and re.search(r'"(?:response|usage|stdout)"\s*:', text))):
        return "provider execution metadata is not artifact content"
    for match in _BRACKET.finditer(text):
        token = match.group(1)
        if token.upper().startswith("NEEDS CLARIFICATION"):
            if allow_clarification:
                continue
            return f"unresolved placeholder [{token}]"
        if re.fullmatch(r"(?:P|US\d+|FR-\d+|AC-\d+|SC-\d+|CHK\d+|[xX ])", token):
            continue
        if (_TEMPLATE_INSTRUCTION.search(token)
                or re.fullmatch(r"[A-Z][A-Z0-9_ ]{2,}", token)):
            return f"unresolved template placeholder [{token}]"
    if "$ARGUMENTS" in text or re.search(
        r"(?:^|:\s*|\*\*\s*)(?:TODO|TBD|TKTK|\?\?\?|<placeholder>)(?:\s|$)",
        text, re.MULTILINE,
    ):
        return "unresolved template placeholder"
    if require_heading and not _HEADING.search(text):
        return "missing Markdown document heading"
    body = [line.strip() for line in text.splitlines()
            if line.strip() and not _HEADING.match(line.strip())
            and not line.strip().startswith(("```", "~~~"))
            and re.search(r"\w", line)]
    if not body:
        return "document has no substantive body"
    return None
