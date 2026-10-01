"""Unit tests for the StagnationDetector and similarity calculation."""
from orchestrator.workflow.stagnation import (
    StagnationDetector,
    content_similarity_ratio,
    normalize_content_for_diff,
)


def test_normalize_content_for_diff():
    text1 = "  This   is \n\n a test   with   spaces.  "
    text2 = "This is a test with spaces."
    assert normalize_content_for_diff(text1) == text2


def test_content_similarity_ratio_exact_and_cosmetic():
    text1 = "# Feature Spec\n\nSome requirement.\n"
    text2 = "  # Feature Spec\n\nSome requirement.  \n"
    # Cosmetic whitespace differences should have similarity 1.0
    assert content_similarity_ratio(text1, text2) == 1.0

    # Substantial changes should have lower similarity
    text3 = "# Entirely different document about architecture and design."
    assert content_similarity_ratio(text1, text3) < 0.5


def test_stagnation_detector_baseline():
    detector = StagnationDetector(stagnation_limit=3)
    report = detector.evaluate(
        status="REVISE",
        issues=["Issue 1", "Issue 2"],
        artifact_text="# Initial spec",
        summary="Needs work",
    )
    assert report.has_progress is True
    assert report.stagnant_streak == 0
    assert report.total_attempts == 1
    assert report.requires_human_intervention is False


def test_stagnation_detector_progress_issues_reduced():
    detector = StagnationDetector(stagnation_limit=3)
    detector.evaluate(
        status="REVISE",
        issues=["Issue 1", "Issue 2", "Issue 3"],
        artifact_text="# Version 1",
    )

    # Version 2 addresses 2 of the issues
    report2 = detector.evaluate(
        status="REVISE",
        issues=["Issue 1"],
        artifact_text="# Version 2 with fixes",
    )
    assert report2.has_progress is True
    assert report2.stagnant_streak == 0
    assert "reduced from 3 to 1" in report2.reason
    assert report2.requires_human_intervention is False


def test_stagnation_detector_progress_issues_resolved():
    detector = StagnationDetector(stagnation_limit=3)
    detector.evaluate(
        status="REVISE",
        issues=["Latency < 200ms is unverified", "Unclear memory bound"],
        artifact_text="# Version 1",
    )

    # Version 2 fixed the latency issue and memory bound, but validator noted 1 minor issue
    report2 = detector.evaluate(
        status="REVISE",
        issues=["Missing test scenario for local read failure"],
        artifact_text="# Version 2 with fixes",
    )
    assert report2.has_progress is True
    assert report2.stagnant_streak == 0
    assert "reduced from 2 to 1" in report2.reason

    # Version 3 maintains 1 issue (plateau)
    report3 = detector.evaluate(
        status="REVISE",
        issues=["Different issue description"],
        artifact_text="# Version 3 with tweaks",
    )
    assert report3.has_progress is False
    assert report3.stagnant_streak == 1
    assert "issue count did not improve" in report3.reason


def test_stagnation_detector_insignificant_change():
    detector = StagnationDetector(stagnation_limit=2, similarity_threshold=0.95)
    text1 = "# Spec\n\nDetail one.\nDetail two."
    detector.evaluate(
        status="REVISE",
        issues=["Missing error handling"],
        artifact_text=text1,
    )

    # Text 2 only changes minor punctuation without fixing the issue
    text2 = "# Spec\n\nDetail one.\nDetail two!"
    report2 = detector.evaluate(
        status="REVISE",
        issues=["Missing error handling"],
        artifact_text=text2,
    )
    assert report2.has_progress is False
    assert report2.stagnant_streak == 1
    assert "Insignificant change" in report2.reason
    assert report2.requires_human_intervention is False

    # Text 3 is also trivial and keeps the same issue -> hits limit 2
    text3 = "# Spec\n\nDetail one..\nDetail two!"
    report3 = detector.evaluate(
        status="REVISE",
        issues=["Missing error handling"],
        artifact_text=text3,
    )
    assert report3.has_progress is False
    assert report3.stagnant_streak == 2
    assert report3.requires_human_intervention is True


def test_stagnation_detector_intervention_reset():
    detector = StagnationDetector(stagnation_limit=2)
    detector.evaluate(
        status="REVISE",
        issues=["Problem A"],
        artifact_text="Same text",
    )
    report2 = detector.evaluate(
        status="REVISE",
        issues=["Problem A"],
        artifact_text="Same text",
    )
    assert report2.stagnant_streak == 1
    report3 = detector.evaluate(
        status="REVISE",
        issues=["Problem A"],
        artifact_text="Same text",
    )
    assert report3.stagnant_streak == 2
    assert report3.requires_human_intervention is True

    # User intervenes and gives go-ahead -> streak is reset
    detector.stagnant_streak = 0
    assert detector.stagnant_streak == 0

    # Next attempt with fixes demonstrates progress
    report4 = detector.evaluate(
        status="PASS",
        issues=[],
        artifact_text="Fixed text",
    )
    assert report4.has_progress is True
    assert report4.requires_human_intervention is False
