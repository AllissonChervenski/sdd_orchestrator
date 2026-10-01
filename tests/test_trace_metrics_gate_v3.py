from orchestrator.config.models import AgentResult
from orchestrator.storage.sqlite import StateStore
from orchestrator.traceability import TraceabilityRecord
from orchestrator.interactive import InteractiveGate


def test_traceability_persists_and_filters_by_requirement(tmp_path):
    store=StateStore(tmp_path/"state.db")
    workflow=store.create_workflow("feature")
    record=TraceabilityRecord("FR-018",["AC-018-01"],["PD-007"],["T018"],["tests/test_queue.py::test_x"],["src/queue.py"],[{"status":"PASS"}],"PASS")
    store.upsert_traceability(workflow,record)
    assert store.list_traceability("FR-018")[0]["record"]["test_ids"]==["tests/test_queue.py::test_x"]
    assert store.list_traceability("FR-999")==[]


def test_provider_and_tdd_metrics_are_collected(tmp_path):
    store=StateStore(tmp_path/"state.db")
    store.record_provider_execution(AgentResult("codex","model-a","test_validator",True,duration=2,structured_output={"status":"PASS"}),"prompt",attempt=1)
    store.record_provider_execution(AgentResult("codex","model-a","test_validator",False,duration=3),"retry",attempt=2)
    store.record_metric("codex","model-a","test_validator","validator_rejection",1)
    store.record_tdd_metrics("workflow","T018",{"red_valid":True,"first_pass_green":False,"green_attempts":2,"regression_failed":True,"test_tampering":False})
    metrics=store.summarize_metrics()
    row=metrics["providers"][0]
    assert row["runs"]==2 and row["success_rate"]==0.5
    assert row["average_retries"]==0.5 and row["validator_rejections"]==1
    assert metrics["tdd"]["average_green_attempts"]==2


def test_interactive_gate_continue_inspect_abort(capsys):
    answers=iter(["inspect","continue","abort"])
    gate=InteractiveGate(input_fn=lambda prompt:next(answers))
    assert gate.confirm("RED","test_designer","agy","model-a",["tests/test_x.py"],[["python","-m","pytest","-q","tests/test_x.py"]])
    assert not gate.confirm("GREEN","coder","opencode","model-b",["src/x.py"],[])
    output=capsys.readouterr().out
    assert "RED" in output and "test_designer" in output and "tests/test_x.py" in output
