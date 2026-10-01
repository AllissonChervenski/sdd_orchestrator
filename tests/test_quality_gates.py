import pytest

from orchestrator.interactive import InteractiveGate
from orchestrator.config.loader import load_config
from orchestrator.config.models import Config
from orchestrator.workflow.quality_gates import (
    analyze_has_critical_findings,
    clarification_questions,
    convergence_outcome,
    run_convergence_loop,
)


def test_extracts_only_explicit_spec_clarification_markers():
    spec = "Requirement [NEEDS CLARIFICATION: Which retention period?].\n[NEEDS CLARIFICATION]"
    assert clarification_questions(spec) == [
        "Which retention period?",
        "Please clarify this requirement.",
    ]


def test_interactive_gate_collects_and_records_clarification_answers(capsys):
    answers = iter(["90 days", "abort"])
    gate = InteractiveGate(input_fn=lambda prompt: next(answers))
    assert gate.ask_clarifications(["Retention period?", "Data region?"]) is None
    assert gate.last_decision == "abort"
    assert gate.events == [{"event": "clarification_answered", "index": 1}]
    assert "Retention period?" in capsys.readouterr().out


def test_headless_clarification_fallback_is_safe_by_default_and_configurable(tmp_path):
    assert Config().human_gates["clarification_fallback"] == "block"
    config_path = tmp_path / "orchestrator.json"
    config_path.write_text('{"human_gates":{"clarification_fallback":"skip"}}')
    assert load_config(config_path).human_gates["clarification_fallback"] == "skip"


def test_analyze_gate_recognizes_critical_count_and_findings():
    assert analyze_has_critical_findings("Critical Issues Count: 2")
    assert analyze_has_critical_findings("| A1 | Coverage | CRITICAL | spec.md | issue | fix |")
    assert not analyze_has_critical_findings("Critical Issues Count: 0")


def test_convergence_requires_append_only_or_explicit_converged_result():
    original = b"# Tasks\n"
    assert convergence_outcome(original, original, "Converged — implementation satisfies requirements") == "converged"
    assert convergence_outcome(original, original + b"\n- [ ] T002 remaining\n", "tasks appended") == "tasks_appended"
    with pytest.raises(ValueError, match="append-only"):
        convergence_outcome(original, b"rewritten", "Converged")
    with pytest.raises(ValueError, match="explicit converged"):
        convergence_outcome(original, original, "Analysis finished")


def test_convergence_loop_reimplements_appended_work_then_converges_again():
    events = []
    outcomes = iter(["tasks_appended", "converged"])
    result = run_convergence_loop(
        3,
        lambda iteration: (events.append(("converge", iteration)), next(outcomes))[1],
        lambda iteration: events.append(("implement", iteration)),
    )
    assert result == 2
    assert events == [
        ("converge", 1), ("implement", 2),
        ("converge", 2),
    ]


def test_convergence_loop_stops_at_limit_without_unbounded_implementation():
    events = []
    with pytest.raises(ValueError, match="max_convergence_iterations=1"):
        run_convergence_loop(
            1,
            lambda iteration: (events.append(("converge", iteration)), "tasks_appended")[1],
            lambda iteration: events.append(("implement", iteration)),
        )
    assert events == [("converge", 1)]


def test_convergence_loop_rejects_zero_limit_before_running_work():
    with pytest.raises(ValueError, match="at least 1"):
        run_convergence_loop(0, lambda _: "converged", lambda _: None)


def test_final_verification_runs_only_after_convergence_stabilizes():
    events = []
    outcomes = iter(["tasks_appended", "converged"])
    limit = 3

    last_attempt = run_convergence_loop(
        limit,
        lambda iteration: (events.append(("converge", iteration)), next(outcomes))[1],
        lambda iteration: events.append(("implement", iteration)),
    )
    events.append(("verify", last_attempt))

    assert last_attempt == 2
    assert events == [
        ("converge", 1), ("implement", 2),
        ("converge", 2), ("verify", 2),
    ]
