import pytest
from orchestrator.tdd import TDDGate
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase, transition


def test_green_cannot_start_without_expected_red():
    with pytest.raises(ValueError): transition(TDDPhase.RED_VERIFY,TDDPhase.GREEN_IMPLEMENT,{"red_result":"INVALID_TEST"})


def test_full_tdd_gate_requires_all_evidence():
    task=TDDTask("T018",["FR-018"],["AC-018-01"]); gate=TDDGate(task)
    assert gate.red("PASS","EXPECTED_FAILURE")
    assert gate.green(True)=="PASS"
    assert gate.refactor(False,True)
    assert gate.review(True,True)
    assert task.phase==TDDPhase.COMPLETE


def test_test_tampering_blocks_green_progression():
    task=TDDTask("T1"); gate=TDDGate(task); gate.red("PASS","EXPECTED_FAILURE")
    assert gate.green(True,tampered=True)=="TEST_TAMPERING"
    assert task.phase==TDDPhase.GREEN_IMPLEMENT


def test_return_to_red_requires_independent_approval():
    task=TDDTask("T1"); gate=TDDGate(task); gate.red("PASS","EXPECTED_FAILURE")
    with pytest.raises(ValueError): task.advance(TDDPhase.RED_GENERATE)
    task.advance(TDDPhase.RED_GENERATE,{"test_change_approved":True})
    assert task.phase==TDDPhase.RED_GENERATE


def test_task_state_persists_evidence(tmp_path):
    task=TDDTask("T1",["FR1"]); task.save(tmp_path/"tdd.json")
    assert '"phase": "ANALYZE"' in (tmp_path/"tdd.json").read_text()
    assert "## FINAL VERIFY" in (tmp_path/"tdd.md").read_text()
