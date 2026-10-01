import pytest
from orchestrator.workflow.engine import Workflow


def test_sdd_stage_transition_is_sequential():
    w=Workflow("id","feature")
    w.advance("CONSTITUTION_VALIDATION")
    with pytest.raises(ValueError): w.advance("TASKS")
