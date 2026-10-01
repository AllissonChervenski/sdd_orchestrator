"""Cost and execution policies use mocked catalogs; no provider is called."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from orchestrator.agents.cost import CostAwareRouter, TaskProfile
from orchestrator.agents.execution_policy import ExecutionPolicyRouter
from orchestrator.agents.router import ModelRouter
from orchestrator.agents.runner import AgentRunner
from orchestrator.config.models import AgentResult, ModelCapabilities, ProviderCapabilities, ValidationResult
from orchestrator.providers.common import extract_usage
from orchestrator.storage.sqlite import StateStore


LADDER = [
    {"level": "LUNA", "provider": "codex", "model": "gpt-6-luna"},
    {"level": "SOL", "provider": "codex", "model": "gpt-6-sol"},
    {"level": "SONNET", "provider": "agy", "model": "claude-sonnet-4-6"},
    {"level": "ASTRA", "provider": "codex", "model": "gpt-6-astra"},
]


def setup(*, mode="observe", profile="balanced", omitted=(), config=None, details=None):
    catalogs = {
        "codex": ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra"],
        "agy": ["claude-sonnet-4-6", "claude-opus-4-6-thinking"],
        "opencode": ["opencode-go/qwen3.8-flash"],
    }
    caps = {
        provider: ProviderCapabilities(
            provider, cli_available=True, models=[model for model in models if model not in omitted],
            supports_model_selection=True, supports_file_editing=True, supports_shell=True,
            supports_json=True, model_details=(details or {}).get(provider, []),
        )
        for provider, models in catalogs.items()
    }
    tiers = {
        "codex": {"gpt-6-luna": {"tiers": ["fast", "balanced", "coding_strong"]},
                  "gpt-6-sol": {"tiers": ["balanced", "strong", "coding_strong"]},
                  "gpt-6-astra": {"tiers": ["strong", "coding_strong"]}},
        "agy": {"claude-sonnet-4-6": {"tiers": ["balanced", "strong", "coding_strong"]},
                "claude-opus-4-6-thinking": {"tiers": ["strong", "coding_strong"]}},
        "opencode": {"opencode-go/qwen3.8-flash": {"tiers": ["coding_strong", "fast"]}},
    }
    router = ModelRouter(caps, {"models_config": {"models": tiers}, "roles": {
        "coder": {"tier": "coding_strong", "preferred_providers": ["codex", "opencode", "agy"]},
        "specification_validator": {"tier": "balanced", "preferred_providers": ["codex", "agy", "opencode"]},
    }, "routing": {"adaptive_routing_mode": "observe", "exploration_rate": 0}})
    policy = {"mode": mode, "profile": profile, "ladder": deepcopy(LADDER),
              "extraordinary_fallback": {"provider": "agy", "model": "claude-opus-4-6-thinking"},
              "economical_coding_models": [{"provider": "opencode", "model": "opencode-go/qwen3.8-flash"}]}
    policy.update(config or {})
    return router, CostAwareRouter(router, policy)


def assess(complexity="LOW", *, role="coder", failures=(), author=None, **options):
    router, cost = setup(**options)
    task = {"task_complexity": complexity}
    base = router.route(role, task=task, author_provider=author)
    return cost.assess(role, base, task, author_provider=author, failures=failures)


def failure(provider, model, category="GREEN_IMPLEMENTATION_FAILURE"):
    return {"provider": provider, "model": model, "category": category}


@pytest.mark.parametrize("complexity", ["LOW", "MEDIUM"])
def test_luna_first_for_low_and_medium(complexity):
    report = assess(complexity)
    assert report["cost_aware_recommendation"] == "codex/gpt-6-luna"
    assert report["recommended_escalation_level"] == "LUNA"


@pytest.mark.parametrize("complexity", ["LOW", "MEDIUM", "HIGH"])
def test_astra_is_not_selected_solely_from_complexity(complexity):
    assert assess(complexity)["recommended_escalation_level"] != "ASTRA"


def test_high_can_start_at_sol():
    assert assess("HIGH")["recommended_escalation_level"] == "SOL"


def test_astra_requires_reason_and_prior_failures():
    report = assess("HIGH")
    astra = next(row for row in report["candidates"] if row["level"] == "ASTRA")
    assert astra["eligibility"] == "ineligible"
    assert "WHY_ASTRA" in " ".join(astra["eligibility_reasons"])
    assert report["astra_escalation_reason"] is None


def test_repeated_luna_failure_unlocks_sol():
    failures = [failure("codex", "gpt-6-luna")] * 2
    report = assess("LOW", failures=failures, omitted={"opencode-go/qwen3.8-flash"})
    assert report["recommended_escalation_level"] == "SOL"
    assert report["previous_models_attempted"] == ["codex/gpt-6-luna"] * 2


def test_repeated_sol_failure_unlocks_sonnet_before_astra():
    failures = [failure("codex", "gpt-6-luna")] * 2 + [failure("codex", "gpt-6-sol")] * 2
    report = assess("LOW", failures=failures, omitted={"opencode-go/qwen3.8-flash"})
    assert report["recommended_escalation_level"] == "SONNET"
    assert report["cost_aware_recommendation"] == "agy/claude-sonnet-4-6"


def test_attributed_full_ladder_failure_allows_audited_astra():
    failures = ([failure("codex", "gpt-6-luna")] * 2 +
                [failure("codex", "gpt-6-sol")] * 2 +
                [failure("agy", "claude-sonnet-4-6", "VALIDATOR_REJECTION")] * 2)
    report = assess("LOW", failures=failures, omitted={"opencode-go/qwen3.8-flash"})
    assert report["recommended_escalation_level"] == "ASTRA"
    assert report["astra_escalation_reason"]
    assert len(report["previous_failure_reasons"]) == 6


@pytest.mark.parametrize("category", ["TIMEOUT", "RATE_LIMIT", "AUTH", "CLI_ERROR", "PROVIDER_FAILURE", "LINT", "INVALID_TEST"])
def test_transient_or_trivial_failure_does_not_escalate(category):
    failures = [failure("codex", "gpt-6-luna", category)] * 3
    assert assess("LOW", failures=failures)["recommended_escalation_level"] == "LUNA"


def test_opus_is_extraordinary_only_and_reports_why():
    report = assess("HIGH")
    opus = next(row for row in report["candidates"] if row["level"] == "OPUS")
    assert opus["eligibility"] == "ineligible"
    assert opus["WHY_OPUS"] == "excluded from normal ladder"
    assert "OPUS" not in [item["level"] for item in LADDER]


def test_manual_opus_override_is_allowed():
    router, cost = setup()
    base = router.route("coder", override_provider="agy", override_model="claude-opus-4-6-thinking")
    report = cost.assess("coder", base, override_model="claude-opus-4-6-thinking")
    opus = next(row for row in report["candidates"] if row["level"] == "OPUS")
    assert opus["eligibility"] == "eligible"


def test_discovery_skips_configured_but_absent_model():
    router, cost = setup(omitted={"gpt-6-luna"})
    task = {"task_complexity": "LOW"}
    report = cost.assess("coder", router.route("coder", task=task), task)
    assert all(row["model"] != "gpt-6-luna" for row in report["candidates"])


def test_known_model_incapability_excludes_it():
    detail = ModelCapabilities("codex", "gpt-6-luna", supports_coding=False)
    router, cost = setup(details={"codex": [detail]})
    task = {"task_complexity": "LOW"}
    report = cost.assess("coder", router.route("coder", task=task), task)
    assert all(row["level"] != "LUNA" for row in report["candidates"])


def test_small_quality_gain_does_not_displace_luna():
    config = {"quality_priors": {"LUNA": {"LOW": .92}, "SOL": {"LOW": .94}}}
    assert assess("LOW", config=config)["recommended_escalation_level"] == "LUNA"


def test_large_quality_gap_can_justify_sol():
    config = {"quality_priors": {"LUNA": {"MEDIUM": .58}, "SOL": {"MEDIUM": .91}}}
    assert assess("MEDIUM", config=config, omitted={"opencode-go/qwen3.8-flash"})["recommended_escalation_level"] == "SOL"


@pytest.mark.parametrize("profile,complexity,expected", [
    ("economical", "MEDIUM", "LUNA"), ("balanced", "MEDIUM", "LUNA"),
    ("quality", "MEDIUM", "SOL"), ("quality", "HIGH", "SONNET"),
])
def test_routing_profiles_keep_astra_last_resort(profile, complexity, expected):
    report = assess(complexity, profile=profile, omitted={"opencode-go/qwen3.8-flash"})
    assert report["recommended_escalation_level"] == expected


def test_observe_does_not_change_base_route():
    report = assess("LOW", mode="observe")
    assert report["actual_route"].provider + "/" + report["actual_route"].model == report["selected_by_policy"]


def test_assist_applies_recommendation():
    report = assess("LOW", mode="assist")
    assert report["actual_route"].provider + "/" + report["actual_route"].model == report["cost_aware_recommendation"]


def test_strong_model_budgets_exclude_exhausted_level():
    router, cost = setup(config={"strong_model_budgets": {"sol": {"task": 1, "workflow": 2}}})
    task = {"task_complexity": "HIGH"}
    report = cost.assess("coder", router.route("coder", task=task), task,
                         usage_counts={"SOL": {"task": 1, "workflow": 1}})
    sol = next(row for row in report["candidates"] if row["level"] == "SOL")
    assert sol["eligibility"] == "ineligible"
    assert "budget exhausted" in " ".join(sol["eligibility_reasons"])


@pytest.mark.parametrize("level", ["SONNET", "ASTRA", "OPUS"])
def test_each_robust_model_budget_can_block(level):
    router, cost = setup(config={"strong_model_budgets": {level.lower(): {"task": 0, "workflow": 0}}, "allow_automatic_opus": True})
    task = {"task_complexity": "HIGH"}
    report = cost.assess("coder", router.route("coder", task=task), task,
                         escalation_reason="audited exceptional task")
    row = next(item for item in report["candidates"] if item["level"] == level)
    assert row["eligibility"] == "ineligible"


def test_opencode_economical_coding_stays_eligible():
    report = assess("LOW")
    item = next(row for row in report["candidates"] if row["provider"] == "opencode")
    assert item["level"] == "CODING_ECONOMY"
    assert item["eligibility"] == "eligible"


def test_author_validator_independence_is_preserved():
    report = assess("LOW", role="specification_validator", author="codex")
    assert report["actual_route"].provider != "codex"
    assert report["actual_route"].independence is True


def test_task_profile_uses_deterministic_metadata():
    profile = TaskProfile.derive("coder", {"task_type": "CODING", "task_complexity": "HIGH",
                                          "risk": "HIGH", "allowed_files": ["a.py", "b.py"],
                                          "estimated_input_tokens": 1000, "estimated_output_tokens": 500})
    assert (profile.task_type, profile.complexity, profile.risk, profile.scope) == ("CODING", "HIGH", "HIGH", "MULTI_FILE")
    assert profile.estimated_input_tokens == 1000


def test_prices_remain_unknown_without_reported_or_configured_data():
    assert all(row["expected_cost"] is None for row in assess("LOW")["candidates"])


def test_cost_estimate_uses_only_explicit_price_and_token_estimates():
    router, cost = setup()
    router.config["models_config"]["models"]["codex"]["gpt-6-luna"].update(
        {"cost_per_million_input_tokens": 1.0, "cost_per_million_output_tokens": 2.0})
    task = {"task_complexity": "LOW", "estimated_input_tokens": 1000, "estimated_output_tokens": 500}
    report = cost.assess("coder", router.route("coder", task=task), task)
    luna = next(row for row in report["candidates"] if row["level"] == "LUNA")
    assert luna["expected_cost"] == pytest.approx(.002)


def test_small_quality_gain_and_higher_configured_cost_keep_luna():
    router, cost = setup()
    models = router.config["models_config"]["models"]["codex"]
    models["gpt-6-luna"].update({"cost_per_million_input_tokens": 1, "cost_per_million_output_tokens": 1})
    models["gpt-6-sol"].update({"cost_per_million_input_tokens": 5, "cost_per_million_output_tokens": 5})
    task = {"task_complexity": "LOW", "estimated_input_tokens": 1000, "estimated_output_tokens": 1000}
    base = replace(router.route("coder", task=task), provider="codex", model="gpt-6-sol")
    report = cost.assess("coder", base, task)
    assert report["cost_aware_recommendation"] == "codex/gpt-6-luna"
    assert report["marginal_quality_gain"] == pytest.approx(.02)
    assert report["marginal_cost"] == pytest.approx(.008)
    assert report["estimated_savings"] == pytest.approx(.008)


def test_partial_price_catalog_does_not_treat_unknown_as_free():
    router, cost = setup()
    task = {"task_complexity": "LOW", "estimated_input_tokens": 1000, "estimated_output_tokens": 1000}
    base = router.route("coder", task=task)
    baseline = cost.assess("coder", base, task)
    router.config["models_config"]["models"]["opencode"]["opencode-go/qwen3.8-flash"].update(
        {"cost_per_million_input_tokens": 10, "cost_per_million_output_tokens": 10})
    priced = cost.assess("coder", base, task)
    before = next(row for row in baseline["candidates"] if row["level"] == "CODING_ECONOMY")
    after = next(row for row in priced["candidates"] if row["level"] == "CODING_ECONOMY")
    assert after["expected_cost"] == pytest.approx(.02)
    assert after["cost_adjusted_score"] == before["cost_adjusted_score"]


def test_ponytail_coder_preserves_nonnegotiable_checks():
    policy = ExecutionPolicyRouter({"prompt_fallback": True}).select(
        "coder", "codex", TaskProfile.derive("coder"))
    assert policy.ponytail_enabled and policy.caveman_enabled
    assert "requirement" in policy.prefix and "test" in policy.prefix
    assert "security" in policy.prefix and "accessibility" in policy.prefix


def test_ponytail_is_not_applied_to_validator():
    policy = ExecutionPolicyRouter({"prompt_fallback": True}).select(
        "specification_validator", "codex", TaskProfile.derive("specification_validator"))
    assert not policy.ponytail_enabled and policy.caveman_enabled


def test_caveman_preserves_structured_output_and_ids():
    policy = ExecutionPolicyRouter({"prompt_fallback": True}).select(
        "test_validator", "codex", TaskProfile.derive("test_validator"))
    assert "JSON" in policy.prefix and "IDs" in policy.prefix


def test_unavailable_native_skill_has_safe_default():
    policy = ExecutionPolicyRouter({}, {"codex": SimpleNamespace(supports_ponytail=None, supports_caveman=None)}).select(
        "coder", "codex", TaskProfile.derive("coder"))
    assert policy.policy_source == "unavailable" and not policy.prefix


def test_compact_policy_uses_fewer_prompt_tokens():
    router = ExecutionPolicyRouter({"prompt_fallback": True})
    compact = router.select("coder", "codex", TaskProfile.derive("coder"))
    standard = router.select("coder", "codex", TaskProfile.derive("coder", {"task_complexity": "HIGH"}))
    assert compact.policy_overhead_estimate < standard.policy_overhead_estimate


def test_usage_is_unknown_if_cli_does_not_report_it():
    assert extract_usage("codex", "CODEX_OK") == {}
    assert extract_usage("opencode", '{"type":"step_finish","part":{"tokens":{"input":12,"output":5},"cost":0.001}}') == {
        "input_tokens": 12, "output_tokens": 5, "total_tokens": 17, "reported_cost": .001}


def test_runner_injects_compact_policies_without_touching_gates():
    router, cost = setup()
    prompts = []

    class FakeProvider:
        def run(self, prompt, role, model, cwd, timeout, permissions):
            prompts.append(prompt)
            return AgentResult("opencode", model, role, True, 0, stdout="done")

    runner = AgentRunner({"opencode": FakeProvider()}, router, cost_router=cost,
                         execution_policy_router=ExecutionPolicyRouter({"prompt_fallback": True}))
    result = runner.run("coder", "Implement FR-018", override_provider="opencode",
                        override_model="opencode-go/qwen3.8-flash", fallback=False)
    assert result.success
    assert result.usage["ponytail_enabled"] and result.usage["caveman_enabled"]
    assert prompts and "FR-018" in prompts[0] and "Preserve requirements" in prompts[0]


def test_runner_enforces_sol_budget_before_provider_call():
    router, cost = setup(config={"strong_model_budgets": {"sol": {"task": 0, "workflow": 0}}})

    class NeverCall:
        def run(self, *args):
            pytest.fail("budgeted provider must not be called")

    runner = AgentRunner({"codex": NeverCall()}, router, cost_router=cost)
    result = runner.run("coder", "task", override_provider="codex", override_model="gpt-6-sol", fallback=False)
    assert result.error == "BUDGET_EXCEEDED: strong model call limit"


def test_runner_blocks_unreasoned_astra_before_provider_call():
    router, cost = setup()

    class NeverCall:
        def run(self, *args):
            pytest.fail("unreasoned Astra must not be called")

    runner = AgentRunner({"codex": NeverCall()}, router, cost_router=cost)
    result = runner.run("coder", "task", override_provider="codex", override_model="gpt-6-astra", fallback=False)
    assert result.error == "ASTRA_ESCALATION_REQUIRED: explicit escalation reason is missing"


def test_malformed_structured_output_is_attributable_feedback():
    router, cost = setup()
    runner = AgentRunner({}, router, cost_router=cost)
    result = AgentResult("codex", "gpt-6-luna", "specification_validator", True)
    verdict = ValidationResult("BLOCKED", [], "Could not parse strict validation output", "specification_validator", result.model, "now")
    runner.record_validation(result, verdict)
    assert runner.model_feedback[0]["category"] == "STRUCTURED_OUTPUT_FAILURE"


def test_contextual_roi_requires_samples_and_measured_values(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    for index in range(2):
        result = AgentResult("codex", "gpt-6-luna", "coder", True, 0, duration=1,
                             usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                    "reported_cost": .01})
        store.record_provider_execution(result, "prompt", workflow_id="W", task_id=f"T{index}",
                                        task_type="CODING", task_complexity="LOW",
                                        outcome={"success": True, "first_pass_green": index == 0, "task_risk": "LOW",
                                                 "total_tokens": 15, "reported_cost": .01})
    assert store.cost_roi(3) == []
    roi = store.cost_roi(2)[0]
    assert roi["cost_per_success"] == pytest.approx(.01)
    assert roi["tokens_per_success"] == pytest.approx(15)
    assert roi["cost_per_first_pass_green"] == pytest.approx(.02)
