from enum import Enum


class TDDPhase(str, Enum):
    ANALYZE = "ANALYZE"
    RED_GENERATE = "RED_GENERATE"
    RED_VERIFY = "RED_VERIFY"
    GREEN_IMPLEMENT = "GREEN_IMPLEMENT"
    GREEN_VERIFY = "GREEN_VERIFY"
    REFACTOR = "REFACTOR"
    REGRESSION_VERIFY = "REGRESSION_VERIFY"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


ALLOWED = {
    TDDPhase.ANALYZE: {TDDPhase.RED_GENERATE, TDDPhase.BLOCKED},
    TDDPhase.RED_GENERATE: {TDDPhase.RED_VERIFY, TDDPhase.BLOCKED},
    TDDPhase.RED_VERIFY: {TDDPhase.GREEN_IMPLEMENT, TDDPhase.RED_GENERATE, TDDPhase.BLOCKED},
    TDDPhase.GREEN_IMPLEMENT: {TDDPhase.GREEN_VERIFY, TDDPhase.RED_GENERATE, TDDPhase.BLOCKED},
    TDDPhase.GREEN_VERIFY: {TDDPhase.GREEN_IMPLEMENT, TDDPhase.REFACTOR, TDDPhase.BLOCKED},
    TDDPhase.REFACTOR: {TDDPhase.REGRESSION_VERIFY, TDDPhase.BLOCKED},
    TDDPhase.REGRESSION_VERIFY: {TDDPhase.REFACTOR, TDDPhase.REVIEW, TDDPhase.BLOCKED},
    TDDPhase.REVIEW: {TDDPhase.COMPLETE, TDDPhase.GREEN_IMPLEMENT, TDDPhase.BLOCKED},
    TDDPhase.COMPLETE: set(), TDDPhase.BLOCKED: set(),
}


def transition(current: TDDPhase | str, target: TDDPhase | str, evidence: dict | None = None) -> TDDPhase:
    current, target = TDDPhase(current), TDDPhase(target)
    evidence = evidence or {}
    if target not in ALLOWED[current]: raise ValueError(f"Illegal TDD transition: {current.value} -> {target.value}")
    if current == TDDPhase.RED_VERIFY and target == TDDPhase.GREEN_IMPLEMENT and evidence.get("red_result") != "EXPECTED_FAILURE":
        raise ValueError("GREEN requires RED EXPECTED_FAILURE")
    if current == TDDPhase.GREEN_IMPLEMENT and target == TDDPhase.RED_GENERATE and evidence.get("test_change_approved") is not True:
        raise ValueError("Return to RED requires independent approval of the test change")
    if current == TDDPhase.GREEN_VERIFY and target == TDDPhase.REFACTOR and evidence.get("green_pass") is not True:
        raise ValueError("REFACTOR requires GREEN pass")
    if current == TDDPhase.REGRESSION_VERIFY and target == TDDPhase.REVIEW and evidence.get("regression_pass") is not True:
        raise ValueError("REVIEW requires regression pass")
    if current == TDDPhase.REVIEW and target == TDDPhase.COMPLETE:
        required = ("test_validated", "verification_pass", "traceability_recorded", "refactor_accounted")
        if any(evidence.get(k) is not True for k in required): raise ValueError("COMPLETE requires validated TDD evidence and verification")
    return target
