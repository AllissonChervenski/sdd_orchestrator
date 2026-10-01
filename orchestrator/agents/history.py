"""Explainable, time weighted routing evidence. No model calls are made here."""
from datetime import datetime, timezone
from math import exp, log


ROLE_TYPES = {
    "constitution": "DOCUMENT", "specification": "SPECIFICATION", "planning": "PLANNING",
    "tasks": "PLANNING", "test_designer": "TEST_DESIGN", "test_validator": "TEST_VALIDATION",
    "coder": "CODING", "refactorer": "REFACTOR", "debugger": "DEBUGGING",
    "code_reviewer": "REVIEW", "final_reviewer": "REVIEW",
}


def classify_task(role, task=None):
    task = task or {}
    kind = str(task.get("task_type") or ROLE_TYPES.get(role) or ("VALIDATION" if "validator" in role else "DOCUMENT")).upper()
    if kind not in {"DOCUMENT", "SPECIFICATION", "PLANNING", "VALIDATION", "CODING", "REFACTOR", "DEBUGGING", "TEST_DESIGN", "TEST_VALIDATION", "REVIEW"}:
        kind = "DOCUMENT"
    complexity = str(task.get("task_complexity") or "").upper()
    if complexity not in {"LOW", "MEDIUM", "HIGH"}:
        size = len(task.get("requirements", [])) + len(task.get("acceptance_criteria", [])) + len(task.get("dependencies", []))
        complexity = "HIGH" if size >= 9 else "MEDIUM" if size >= 3 else "LOW"
    return kind, complexity


def confidence(samples, minimum=10):
    """Linear to 50 samples, reduced below the configured minimum."""
    if samples <= 0: return 0.0
    return min(1.0, samples / max(50, minimum * 5)) * min(1.0, samples / max(1, minimum))


def decay_weight(timestamp, half_life_days=30, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        when = datetime.fromisoformat(timestamp)
        if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - when).total_seconds() / 86400)
    except (ValueError, TypeError):
        age = 0.0
    return exp(-log(2) * age / max(0.01, half_life_days))


def summarize(rows, half_life_days=30, now=None):
    """Rows retain raw sample count; only their contribution to rates decays."""
    if not rows: return {"runs": 0, "samples": 0, "confidence": 0.0, "historical_score": 0.0}
    weights = [decay_weight(row.get("timestamp"), half_life_days, now) for row in rows]
    total = sum(weights)
    def avg(key, default=0.0):
        return sum(w * float(row.get(key, default) if row.get(key, default) is not None else default) for row, w in zip(rows, weights)) / total
    rates = {
        "runs": len(rows), "samples": len(rows), "effective_samples": total,
        "success_rate": avg("success"), "first_pass_success_rate": avg("first_pass_success"),
        "average_attempts": avg("attempts", 1), "average_latency": avg("latency"),
        "structured_output_failure_rate": 1 - avg("structured_output_valid", 1),
        "validator_rejection_rate": 1 - avg("validator_accepted", 1),
        "regression_failure_rate": 1 - avg("regression_passed", 1),
        "blocked_rate": avg("blocked"), "provider_failure_rate": avg("provider_failure"),
        "first_pass_green_rate": avg("first_pass_green"),
        "average_green_attempts": avg("green_attempts"),
        "review_rejection_rate": 1 - avg("review_accepted", 1),
    }
    # Centered near zero; policy preference still dominates until evidence is substantial.
    rates["historical_score"] = round(30 * (rates["success_rate"] - .5) + 12 * (rates["first_pass_success_rate"] - .5)
        - 8 * rates["validator_rejection_rate"] - 8 * rates["regression_failure_rate"]
        - 12 * rates["blocked_rate"] - 12 * rates["provider_failure_rate"]
        - min(8, rates["average_latency"] / 60), 3)
    return rates
