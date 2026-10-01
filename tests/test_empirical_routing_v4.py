from datetime import datetime, timedelta, timezone

from orchestrator.agents.history import classify_task, confidence, decay_weight, summarize
from orchestrator.agents.router import ModelRouter
from orchestrator.config.models import ProviderCapabilities


def _row(model, success, days=0):
    return {"provider":"codex","model":model,"role":"coder","task_type":"CODING","task_complexity":"LOW",
        "success":success,"first_pass_success":success,"attempts":1,"latency":1,
        "timestamp":(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()}


def _router(mode="observe", history=None, rng=None):
    caps={name:ProviderCapabilities(name,cli_available=True,models=models,supports_model_selection=True,supports_file_editing=True,supports_shell=True)
          for name,models in {"codex":["a","b"],"agy":["c"]}.items()}
    config={"roles":{"coder":{"tier":"coding_strong","preferred_providers":["codex","agy"]}},
        "models_config":{"models":{"codex":{"a":{"tiers":["coding_strong"]},"b":{"tiers":["coding_strong"]}},"agy":{"c":{"tiers":["coding_strong"]}}}},
        "routing":{"adaptive_routing_mode":mode,"historical_min_samples":10,"exploration_rate":0,"decay_half_life_days":30}}
    return ModelRouter(caps,config,history=history,rng=rng)


def test_classification_uses_metadata_and_deterministic_size():
    assert classify_task("coder",{"requirements":["FR-1"],"task_complexity":"HIGH"})==("CODING","HIGH")
    assert classify_task("test_validator",{})==("TEST_VALIDATION","LOW")


def test_sample_confidence_and_temporal_decay():
    assert confidence(0)==0
    assert 0<confidence(5)<confidence(20)<confidence(50)==1
    assert decay_weight((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),30)<.51
    stats=summarize([_row("a",0,days=100),_row("a",1)],half_life_days=10)
    assert stats["success_rate"]>.99
    assert stats["effective_samples"]<2


def test_observe_shows_recommendation_without_changing_policy():
    history=[_row("a",0) for _ in range(50)]+[_row("b",1) for _ in range(50)]
    route=_router(history=history).route("coder")
    assert route.model=="a"
    assert route.selected_by_policy=="codex/a"
    assert route.historical_recommendation=="codex/b"
    assert route.selection_mode=="exploitation"
    assert route.candidates[0]["historical_confidence"]>.9


def test_assist_can_select_better_observed_model():
    history=[_row("a",0) for _ in range(50)]+[_row("b",1) for _ in range(50)]
    route=_router("assist",history).route("coder")
    assert route.model=="b"
    assert route.score>route.candidates[-1]["final_score"]


def test_assist_can_change_provider_after_sufficient_role_evidence():
    failed=[_row(model,0) for model in ("a","b") for _ in range(50)]
    successful=[{**_row("c",1),"provider":"agy"} for _ in range(50)]
    assert _router("observe",failed+successful).route("coder").provider=="codex"
    selected=_router("assist",failed+successful).route("coder")
    assert selected.provider=="agy"
    assert selected.historical_recommendation=="agy/c"


def test_task_type_and_complexity_history_is_used_when_sample_is_sufficient():
    low_good=[_row("a",1) for _ in range(50)]
    low_bad=[_row("b",0) for _ in range(50)]
    high_good=[{**_row("b",1),"task_complexity":"HIGH"} for _ in range(20)]
    high_bad=[{**_row("a",0),"task_complexity":"HIGH"} for _ in range(20)]
    router=_router("assist",low_good+low_bad+high_good+high_bad)
    assert router.route("coder",task={"task_complexity":"LOW"}).model=="a"
    assert router.route("coder",task={"task_complexity":"HIGH"}).model=="b"


def test_exploration_is_limited_and_explain_is_explicit():
    class Draw:
        def random(self): return 0
        def choice(self,values): return values[0]
    router=_router("assist",rng=Draw())
    router.config["routing"]["exploration_rate"]=1
    route=router.route("coder")
    assert route.selection_mode=="exploration"
    assert route.model=="b"
    assert router.route("coder",high_risk=True).selection_mode=="exploitation"
    assert router.route("coder",blocked=True).selection_mode=="exploitation"
    report=router.explain("coder",{"id":"T018","task_complexity":"HIGH"})
    assert report["task_complexity"]=="HIGH"
    assert all({"base_score","historical_score","historical_confidence","penalties","final_score"}<=row.keys() for row in report["candidates"])
