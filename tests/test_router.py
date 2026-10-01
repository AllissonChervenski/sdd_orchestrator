from types import SimpleNamespace
from orchestrator.agents.plan import build_route_plan, format_route
from orchestrator.agents.router import ModelRouter, choose_model


def cap(models=(),*,available=True,shell=True,capabilities=()):
    return SimpleNamespace(cli_available=available,models=list(models),supports_model_selection=True,supports_file_editing=True,supports_shell=shell,supports_json=True,capabilities=list(capabilities),metadata={})


def test_validator_prefers_different_provider_from_author():
    router=ModelRouter({"agy":cap(["gemini-flash-medium"]),"codex":cap(["gpt-6-sol"])})
    route=router.route("specification_validator",author_provider="agy")
    assert route.provider=="codex"
    assert route.independence is True
    assert "different from author provider agy" in route.reason


def test_coder_prefers_provider_with_agentic_coding_capability():
    router=ModelRouter({"agy":cap(["claude-sonnet"],capabilities=["CODING","FILE_EDITING"]),"opencode":cap(["provider/kimi-code"],capabilities=["AGENTIC_CODING"])})
    route=router.route("test_designer")
    assert route.provider=="opencode"


def test_falls_back_when_preferred_provider_unavailable():
    caps={"opencode":cap(["p/code"],available=False),"codex":cap(["gpt-6-sol"])}
    route=ModelRouter(caps).route("coder")
    assert route.provider=="codex"


def test_single_provider_allows_self_validation_and_marks_it():
    router=ModelRouter({"agy":cap(["gemini-flash-medium"])})
    route=router.route("specification_validator",author_provider="agy")
    assert route.provider=="agy"
    assert route.independence is False
    assert "self-validation permitted" in route.reason


def test_model_tier_selects_tier_fit_not_first_catalog_model():
    models=["gpt-6-astra","gpt-6-sol","gpt-6-luna"]
    assert choose_model(models,"fast")[0]=="gpt-6-luna"
    assert choose_model(models,"balanced")[0]=="gpt-6-sol"
    assert choose_model(models,"strong")[0]=="gpt-6-astra"


def test_fast_task_does_not_select_strong_catalog_model():
    model,reason,_=choose_model(["model/opus-frontier","model/flash-fast"],"fast")
    assert model=="model/flash-fast"
    assert "tier-name heuristic" in reason


def test_role_preference_is_not_overruled_by_stronger_model_or_history():
    router=ModelRouter(
        {"agy":cap(["gemini-flash-medium"]),"codex":cap(["gpt-6-astra"])},
        metrics={("codex","gpt-6-astra","constitution"):{"success_rate":1.0}},
    )
    assert router.route("constitution").provider=="agy"


def test_dry_run_plan_contains_reason_and_independence():
    roles=["specification","specification_validator","test_designer","coder","code_reviewer"]
    router=ModelRouter({"agy":cap(["gemini-flash-medium"]),"opencode":cap(["provider/kimi-k2.7-code"]),"codex":cap(["gpt-6-astra","gpt-6-sol"])})
    plan=build_route_plan(router,roles)
    assert plan["specification_validator"].provider!=plan["specification"].provider
    assert plan["specification_validator"].independence is True
    assert plan["code_reviewer"].provider!=plan["coder"].provider
    assert all(route.reason and route.tier for route in plan.values())
    rendered=format_route("specification_validator",plan["specification_validator"])
    assert "reason:" in rendered and "independence=true" in rendered and "BALANCED".lower() in rendered.lower()


def test_independent_route_returns_flag():
    caps={n:cap() for n in ("agy","codex")}
    route,independent=ModelRouter(caps).independent_route("code_reviewer","agy")
    assert route.provider=="codex" and independent
