"""Deterministic stagnation and insignificant change detector for agent review loops."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Sequence


def normalize_content_for_diff(text: str) -> str:
    """Strip whitespace variations, indentation, and formatting noise."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_similarity_ratio(a: str, b: str) -> float:
    """Calculate normalized similarity ratio between 0.0 and 1.0."""
    norm_a = normalize_content_for_diff(a)
    norm_b = normalize_content_for_diff(b)
    if not norm_a and not norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0
    return difflib.SequenceMatcher(None, norm_a, norm_b).ratio()


@dataclass(frozen=True)
class StagnationReport:
    has_progress: bool
    stagnant_streak: int
    total_attempts: int
    reason: str
    requires_human_intervention: bool
    similarity_ratio: float
    stagnation_details: dict[str, Any] = field(default_factory=dict)


class StagnationDetector:
    """Detects when an agent is not making meaningful progress or making insignificant changes."""

    def __init__(
        self,
        stagnation_limit: int = 3,
        similarity_threshold: float = 0.98,
    ):
        self.stagnation_limit = max(1, stagnation_limit)
        self.similarity_threshold = similarity_threshold
        self.stagnant_streak = 0
        self.total_attempts = 0
        self.history: list[dict[str, Any]] = []

    def evaluate(
        self,
        status: str,
        issues: Sequence[str],
        artifact_text: str | None = None,
        summary: str = "",
    ) -> StagnationReport:
        self.total_attempts += 1
        current_issues = [str(i).strip() for i in issues if str(i).strip()]
        current_text = artifact_text or ""

        if status == "PASS":
            return StagnationReport(
                has_progress=True,
                stagnant_streak=0,
                total_attempts=self.total_attempts,
                reason="Validation passed",
                requires_human_intervention=False,
                similarity_ratio=0.0,
            )

        if not self.history:
            self.history.append({
                "text": current_text,
                "issues": current_issues,
                "summary": summary,
            })
            return StagnationReport(
                has_progress=True,
                stagnant_streak=0,
                total_attempts=self.total_attempts,
                reason="Initial baseline registered",
                requires_human_intervention=False,
                similarity_ratio=0.0,
                stagnation_details={"issues_count": len(current_issues)},
            )

        prev = self.history[-1]
        prev_text = prev["text"]
        prev_issues = prev["issues"]

        sim_ratio = content_similarity_ratio(prev_text, current_text)
        has_progress = False
        reason = ""

        # Case 1: Insignificant textual change (similarity >= threshold)
        if sim_ratio >= self.similarity_threshold:
            self.stagnant_streak += 1
            has_progress = False
            reason = (
                f"Insignificant change ({sim_ratio:.1%} similarity) without addressing validator requirements"
            )
        # Case 2: Number of issues reduced (measurable progress)
        elif len(current_issues) < len(prev_issues):
            has_progress = True
            self.stagnant_streak = 0
            reason = f"Progress: issues count reduced from {len(prev_issues)} to {len(current_issues)}"
        # Case 3: Identical issues repeated
        elif set(current_issues) == set(prev_issues):
            self.stagnant_streak += 1
            has_progress = False
            reason = "Stagnation: identical issues repeated without addressing validator requirements"
        # Case 4: Issues count plateaued or increased without passing
        else:
            self.stagnant_streak += 1
            has_progress = False
            reason = f"Stagnation: issue count did not improve ({len(prev_issues)} -> {len(current_issues)})"

        self.history.append({
            "text": current_text,
            "issues": current_issues,
            "summary": summary,
        })

        requires_intervention = self.stagnant_streak >= self.stagnation_limit

        return StagnationReport(
            has_progress=has_progress,
            stagnant_streak=self.stagnant_streak,
            total_attempts=self.total_attempts,
            reason=reason,
            requires_human_intervention=requires_intervention,
            similarity_ratio=sim_ratio,
            stagnation_details={
                "prev_issues_count": len(prev_issues),
                "curr_issues_count": len(current_issues),
                "stagnant_streak": self.stagnant_streak,
                "latest_issues": current_issues,
                "summary": summary,
            },
        )
