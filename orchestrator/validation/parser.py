import json
import re
from typing import Any
from orchestrator.config.models import ValidationResult
from .schemas import validation_result


def _extract(text: str):
    text = text.strip()
    def _unwrap(data):
        if isinstance(data, dict):
            if data.get("status") not in {"PASS", "REVISE", "BLOCKED"} and "response" in data:
                inner = _extract(data["response"]) if isinstance(data["response"], str) else (data["response"] if isinstance(data["response"], dict) else None)
                if isinstance(inner, dict):
                    return inner
        return data

    try: return _unwrap(json.loads(text))
    except ValueError: pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try: return _unwrap(json.loads(fenced.group(1)))
        except ValueError: pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try: return _unwrap(json.loads(text[start:end+1]))
        except ValueError: pass
    return None


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"(?is)<(thought|thinking|reasoning)>.*?</\1>", "", text)
    cleaned = re.sub(r"(?is)^<(thought|thinking|reasoning)>.*", "", cleaned)
    cleaned = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*['\"]?[^\s,'\"]+['\"]?",
        r"\1=[REDACTED]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bbearer\s+[a-zA-Z0-9_\-\.]{8,}", "Bearer [REDACTED]", cleaned)
    cleaned = re.sub(r"\b(ghp_[a-zA-Z0-9]{20,}|sk-[a-zA-Z0-9]{20,}|AIza[a-zA-Z0-9_\-]{30,})", "[REDACTED]", cleaned)
    return cleaned.strip()


def _is_codex_jsonl(text: str) -> bool:
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    try:
        data = json.loads(first)
        if isinstance(data, dict):
            event_type = data.get("type", "")
            if event_type in {
                "thread.started",
                "turn.started",
                "item.started",
                "item.completed",
                "turn.completed",
            }:
                return True
    except (ValueError, TypeError):
        if first.startswith('{"type":') and any(k in first for k in ('"thread.', '"turn.', '"item.')):
            return True
    return False


def _parse_contract(text: str, validator: str, model: str | None = None) -> ValidationResult:
    obj = _extract(text)
    if not isinstance(obj, dict):
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
    status = obj.get("status")
    if status not in {"PASS", "REVISE", "BLOCKED"}:
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
    raw_issues = obj.get("issues")
    if not isinstance(raw_issues, list):
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
    issues: list[str] = []
    for item in raw_issues:
        if not isinstance(item, str):
            return validation_result("PARSE_ERROR", [], "Malformed issue schema", validator, model, text)
        issues.append(item)
    summary_val = obj.get("summary")
    if summary_val is None:
        summary_val = obj.get("reason", "")
    if not isinstance(summary_val, str):
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
    return validation_result(status, issues, summary_val, validator, model, text)


def parse_codex_validation(text: str, validator: str, model: str | None = None) -> ValidationResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)

    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
        if not isinstance(event, dict):
            return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
        events.append(event)

    agent_messages: list[str] = []
    for event in events:
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                msg_text = item.get("text")
                if isinstance(msg_text, str):
                    agent_messages.append(msg_text)

    if not agent_messages:
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)

    final_text = agent_messages[-1]
    res = _parse_contract(final_text, validator, model)
    res.raw_output = text
    return res


def _is_opencode_jsonl(text: str) -> bool:
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    try:
        data = json.loads(first)
        if isinstance(data, dict):
            event_type = data.get("type", "")
            if event_type in {"step_start", "step_finish", "text"} or ("sessionID" in data and "part" in data):
                return True
    except (ValueError, TypeError):
        if first.startswith('{"type":') and any(k in first for k in ('"step_start"', '"step_finish"', '"part"')):
            return True
    return False


def parse_opencode_validation(text: str, validator: str, model: str | None = None) -> ValidationResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parts: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    if not parts:
        return validation_result("PARSE_ERROR", [], "Could not parse strict validation output", validator, model, text)
    extracted = "".join(parts)
    res = _parse_contract(extracted, validator, model)
    res.raw_output = text
    return res


def parse_validation(text: str, validator: str, model: str | None = None, provider: str | None = None) -> ValidationResult:
    if provider == "opencode" or (provider is None and _is_opencode_jsonl(text)):
        if _is_opencode_jsonl(text):
            return parse_opencode_validation(text, validator, model)
        if provider == "opencode":
            obj = _extract(text)
            if isinstance(obj, dict) and obj.get("status") in {"PASS", "REVISE", "BLOCKED"}:
                return _parse_contract(text, validator, model)
            return parse_opencode_validation(text, validator, model)
    if provider == "codex" or (provider is None and _is_codex_jsonl(text)):
        if _is_codex_jsonl(text):
            return parse_codex_validation(text, validator, model)
        if provider == "codex":
            obj = _extract(text)
            if isinstance(obj, dict) and obj.get("status") in {"PASS", "REVISE", "BLOCKED"}:
                return _parse_contract(text, validator, model)
            return parse_codex_validation(text, validator, model)
    return _parse_contract(text, validator, model)
