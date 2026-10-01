import json

import pytest

from orchestrator.workflow.quality_gates import convergence_outcome


@pytest.mark.parametrize("response", ["", "not converged", "Not yet converged", "No changes required"])
def test_provider_success_without_explicit_convergence_does_not_pass(response):
    envelope = json.dumps({
        "conversation_id": "converged-is-not-a-verdict",
        "status": "SUCCESS", "response": response,
        "duration_seconds": 1.0, "usage": {},
    })
    with pytest.raises(ValueError, match="explicit converged"):
        convergence_outcome(b"tasks", b"tasks", envelope)


def test_convergence_decodes_only_explicit_envelope_response():
    envelope = json.dumps({"status": "SUCCESS", "response": "✅ Converged — all requirements satisfied."})
    assert convergence_outcome(b"tasks", b"tasks", envelope) == "converged"


def test_tasks_append_is_detected_from_files_despite_empty_provider_response():
    envelope = json.dumps({"status": "SUCCESS", "response": ""})
    assert convergence_outcome(b"tasks", b"tasks\nnew task", envelope) == "tasks_appended"


def test_converged_provider_response_cannot_authorize_a_tasks_rewrite():
    envelope = json.dumps({"status": "SUCCESS", "response": "Converged"})
    with pytest.raises(ValueError, match="append-only"):
        convergence_outcome(b"original tasks", b"rewritten tasks", envelope)
