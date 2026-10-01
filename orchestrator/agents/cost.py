"""Cost recommendations layered over the existing capability/independence router.

Unknown prices remain unknown. Model IDs are desired configuration entries and are
used only when the current provider catalog confirms availability.
"""
from dataclasses import asdict, dataclass

from orchestrator.agents.history import classify_task
from orchestrator.agents.router import Route
from orchestrator.agents.roles import ROLES


CODING_TYPES={"CODING","TEST_DESIGN","REFACTOR","DEBUGGING"}
CAPABILITY_FAILURES={"MODEL_CAPABILITY_FAILURE","GREEN_IMPLEMENTATION_FAILURE","VALIDATOR_REJECTION","REVIEW_REJECTION","STRUCTURED_OUTPUT_FAILURE"}
NON_CAPABILITY_FAILURES={"PROVIDER_FAILURE","TIMEOUT","RATE_LIMIT","QUOTA_EXHAUSTED","QUOTA","PROVIDER_UNAVAILABLE","AUTH","CLI_ERROR","LINT","FORMATTING","TYPO","INFRASTRUCTURE_FAILURE","INVALID_TEST"}
DEFAULT_QUALITY={
    "LUNA":{"LOW":.92,"MEDIUM":.84,"HIGH":.60},
    "SOL":{"LOW":.94,"MEDIUM":.90,"HIGH":.84},
    "SONNET":{"LOW":.95,"MEDIUM":.92,"HIGH":.90},
    "ASTRA":{"LOW":.97,"MEDIUM":.95,"HIGH":.94},
    "OPUS":{"LOW":.97,"MEDIUM":.95,"HIGH":.94},
    "CODING_ECONOMY":{"LOW":.90,"MEDIUM":.81,"HIGH":.62},
    "OTHER":{"LOW":.82,"MEDIUM":.76,"HIGH":.60},
}
DEFAULT_BUDGETS={
    "SOL":{"task":3,"workflow":20},
    "SONNET":{"task":2,"workflow":8},
    "ASTRA":{"task":1,"workflow":2},
    "OPUS":{"task":0,"workflow":0},
}
LEVELS=("LUNA","SOL","SONNET","ASTRA")


@dataclass(frozen=True)
class TaskProfile:
    task_type: str
    complexity: str
    risk: str
    scope: str
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None

    @classmethod
    def derive(cls, role, task=None):
        task=task or {}
        kind,complexity=classify_task(role,task)
        risk=str(task.get("risk") or ("HIGH" if role=="constitution" else "LOW")).upper()
        if risk not in {"LOW","MEDIUM","HIGH"}: risk="MEDIUM"
        paths=task.get("allowed_files") or task.get("production_files") or []
        scope=str(task.get("scope") or ("ARCHITECTURAL" if task.get("architectural_change") else "MULTI_FILE" if len(paths)>1 else "LOCAL")).upper()
        if scope not in {"LOCAL","MULTI_FILE","ARCHITECTURAL"}: scope="LOCAL"
        return cls(kind,complexity,risk,scope,_positive_int(task.get("estimated_input_tokens") or task.get("estimated_context_tokens")),_positive_int(task.get("estimated_output_tokens")))


def _positive_int(value):
    try: return int(value) if int(value)>0 else None
    except (TypeError,ValueError): return None


def _identity(item): return f"{item['provider']}/{item['model']}"


class CostAwareRouter:
    def __init__(self, model_router, config=None, store=None, workflow_id=None):
        self.router=model_router
        self.config=config or {}
        self.store=store
        self.workflow_id=workflow_id

    def _catalog_has(self, provider, model):
        cap=self.router.capabilities.get(provider)
        if not cap or not cap.cli_available or not cap.supports_model_selection or model not in cap.models: return False
        details=next((item for item in cap.model_details if item.model_id==model),None)
        return details is None or details.available is not False

    def _level(self, provider, model, profile):
        for index,item in enumerate(self.config.get("ladder",[])):
            if item.get("provider")==provider and item.get("model")==model:
                return str(item.get("level") or LEVELS[min(index,3)]).upper()
        extraordinary=self.config.get("extraordinary_fallback",{})
        if extraordinary.get("provider")==provider and extraordinary.get("model")==model: return "OPUS"
        if profile.task_type in CODING_TYPES and any(item.get("provider")==provider and item.get("model")==model for item in self.config.get("economical_coding_models",[])):
            return "CODING_ECONOMY"
        return "OTHER"

    def _usage(self, level, provider, model, task_id):
        if not self.store or not self.workflow_id: return {"task":0,"workflow":0}
        return {"task":self.store.count_provider_model_calls(self.workflow_id,provider,model,task_id),
                "workflow":self.store.count_provider_model_calls(self.workflow_id,provider,model)}

    def assess(self, role, base_route, task=None, author_provider=None, failures=(), override_model=None, escalation_reason=None, usage_counts=None):
        profile=TaskProfile.derive(role,task)
        mode=str(self.config.get("mode","observe")).lower()
        routing_profile=str(self.config.get("profile","balanced")).lower()
        if routing_profile not in {"economical","balanced","quality"}: routing_profile="balanced"
        failures=[item for item in failures if item.get("category") in CAPABILITY_FAILURES]
        def failed(level):
            return [item for item in failures if self._level(item.get("provider"),item.get("model"),profile)==level]
        retry_limit=max(1,int(self.config.get("capability_failures_to_escalate",2)))
        coding_economy_fail=len(failed("CODING_ECONOMY"))>=retry_limit
        luna_fail=len(failed("LUNA"))>=retry_limit
        sol_fail=len(failed("SOL"))>=retry_limit
        sonnet_fail=len(failed("SONNET"))>=retry_limit
        max_level=1 if profile.complexity=="HIGH" else 0
        if routing_profile=="quality" and profile.complexity=="HIGH": max_level=2
        if coding_economy_fail or luna_fail: max_level=max(max_level,1)
        if sol_fail: max_level=max(max_level,2)
        why_astra=escalation_reason or ("Luna, Sol and Sonnet had repeated attributable failures" if luna_fail and sol_fail and sonnet_fail else None)
        if why_astra: max_level=3
        threshold={"economical":.75,"balanced":.80,"quality":.85}[routing_profile]
        if profile.risk=="HIGH": threshold+=.05
        rows=[]
        for source in base_route.candidates:
            provider,model=source["provider"],source["model"]
            if not model or not self._catalog_has(provider,model): continue
            level=self._level(provider,model,profile)
            level_index=LEVELS.index(level) if level in LEVELS else (0 if level=="CODING_ECONOMY" else 1 if level=="OTHER" else 4)
            model_cfg=self.router.config.get("models_config",{}).get("models",{}).get(provider,{}).get(model,{})
            detail=next((item for item in self.router.capabilities[provider].model_details if item.model_id==model),None)
            context=getattr(detail,"context_window",None)
            reasons=[]
            required=ROLES[role].required_capabilities
            model_flags={"CODING":"supports_coding","AGENTIC_CODING":"supports_agentic_work",
                "REASONING":"supports_reasoning","STRUCTURED_OUTPUT":"supports_structured_output",
                "FILE_EDITING":"supports_file_editing","SHELL":"supports_shell"}
            for capability in required:
                flag=model_flags.get(capability)
                if flag and getattr(detail,flag,None) is False:
                    reasons.append(f"model lacks {capability}")
            if profile.estimated_input_tokens and context and profile.estimated_input_tokens>context: reasons.append("context_window_exceeded")
            if level=="OTHER" and source.get("tier_source") not in {"config","provider_metadata"}: reasons.append("unverified_model_fit")
            if level=="OPUS" and not (override_model==model or self.config.get("allow_automatic_opus",False)):
                reasons.append("extraordinary_fallback_requires_override")
            if level=="ASTRA" and not why_astra: reasons.append("WHY_ASTRA required: no attributable escalation evidence")
            if level_index>max_level and level not in {"OPUS"}: reasons.append(f"level {level} not unlocked")
            if level in {"LUNA","SOL","SONNET","CODING_ECONOMY"} and len(failed(level))>=retry_limit:
                reasons.append(f"{level} had repeated attributable failures")
            limits={**DEFAULT_BUDGETS.get(level,{}),**self.config.get("strong_model_budgets",{}).get(level.lower(),{})}
            counts=(usage_counts or {}).get(level)
            if counts is None and level in DEFAULT_BUDGETS:
                counts=self._usage(level,provider,model,(task or {}).get("id"))
            counts=counts or {"task":0,"workflow":0}
            # A deliberate Opus override is the only exception to the default
            # zero automatic budget. Other strong-model caps apply to overrides.
            manual_opus=level=="OPUS" and override_model==model
            if level in DEFAULT_BUDGETS and not manual_opus:
                if counts.get("task",0)>=limits.get("task",999999) or counts.get("workflow",0)>=limits.get("workflow",999999):
                    reasons.append(f"{level} call budget exhausted")
            configured_prior=self.config.get("quality_priors",{}).get(model) or self.config.get("quality_priors",{}).get(level)
            prior=configured_prior or DEFAULT_QUALITY.get(level,DEFAULT_QUALITY["OTHER"])
            expected_quality=float(prior.get(profile.complexity,DEFAULT_QUALITY["OTHER"][profile.complexity]))
            stats=source.get("historical_metrics") or {}
            conf=float(source.get("historical_confidence") or 0)
            if conf>0 and stats.get("runs",0):
                observed=.65*float(stats.get("success_rate",0))+.35*float(stats.get("first_pass_success_rate",0))
                expected_quality=(1-conf)*expected_quality+conf*observed
            expected_quality=max(0.0,min(1.0,expected_quality))
            retry_risk=max(0.0,min(1.0,1-expected_quality))
            cost_input=model_cfg.get("cost_per_million_input_tokens")
            cost_output=model_cfg.get("cost_per_million_output_tokens")
            expected_cost=None
            if all(isinstance(value,(int,float)) for value in (cost_input,cost_output)) and profile.estimated_input_tokens and profile.estimated_output_tokens:
                expected_cost=(profile.estimated_input_tokens*cost_input+profile.estimated_output_tokens*cost_output)/1_000_000
            if expected_quality<threshold: reasons.append(f"quality {expected_quality:.2f} below threshold {threshold:.2f}")
            capability_fit=0.0 if any(reason.startswith(("model lacks ","context_window_exceeded","unverified_model_fit")) for reason in reasons) else 1.0
            cost_efficiency=expected_quality/expected_cost if expected_cost and expected_cost>0 else None
            adjusted=100*expected_quality + .03*float(source["base_score"]) - 15*retry_risk - level_index*3
            rows.append({"provider":provider,"model":model,"level":level,"escalation_level":level_index,"capability_fit":capability_fit,
                "expected_quality":round(expected_quality,4),"quality_source":"history_blend" if conf else "config_prior" if configured_prior else "heuristic_prior",
                "historical_confidence":conf,"expected_cost":expected_cost,"retry_risk":round(retry_risk,4),
                "cost_efficiency_score":cost_efficiency,"base_score":source["base_score"],"cost_adjusted_score":round(adjusted,3),
                "eligibility":"eligible" if not reasons else "ineligible","eligibility_reasons":reasons,
                "tier":source["tier"],"tier_source":source["tier_source"],"independence":base_route.independence,
                "WHY_ASTRA":why_astra if level=="ASTRA" else None,
                "WHY_OPUS":"manual override" if level=="OPUS" and override_model==model else ("configured extraordinary fallback" if level=="OPUS" and self.config.get("allow_automatic_opus",False) else "excluded from normal ladder" if level=="OPUS" else None)})
        eligible=[row for row in rows if row["eligibility"]=="eligible"]
        # An independent provider may be available only at a higher normal level.
        if not eligible:
            for row in rows:
                if row["level"] not in {"ASTRA","OPUS"} and all(reason.startswith("level ") for reason in row["eligibility_reasons"]):
                    row["eligibility"]="eligible"; row["eligibility_reasons"]=["independence/availability required next normal level"]
            eligible=[row for row in rows if row["eligibility"]=="eligible"]
        # A missing quote is unknown, not zero. Apply the price term only when
        # every eligible option has comparable configured price/token estimates.
        if eligible and all(row["expected_cost"] is not None for row in eligible):
            for row in eligible:
                penalty=min(30,row["expected_cost"]*float(self.config.get("cost_penalty_per_unit",10)))
                row["cost_adjusted_score"]=round(row["cost_adjusted_score"]-penalty,3)
                row["cost_penalty"]=round(penalty,3)
        # Unknown prices are not treated as higher prices. The configured ladder
        # supplies the conservative ordering until comparable prices are known.
        eligible.sort(key=lambda row:(row["escalation_level"],-row["cost_adjusted_score"],row["expected_cost"] if row["expected_cost"] is not None else float("inf")))
        recommended=eligible[0] if eligible else None
        selected=next((row for row in rows if row["provider"]==base_route.provider and row["model"]==base_route.model),None)
        def key(row): return _identity(row) if row else None
        quality_delta=(recommended["expected_quality"]-selected["expected_quality"]) if recommended and selected else None
        gain=abs(quality_delta) if quality_delta is not None else None
        cost_delta=(recommended["expected_cost"]-selected["expected_cost"]) if recommended and selected and recommended["expected_cost"] is not None and selected["expected_cost"] is not None else None
        marginal_cost=abs(cost_delta) if cost_delta is not None else None
        savings=-cost_delta if cost_delta is not None else None
        if recommended and mode in {"assist","adaptive"}:
            actual=Route(recommended["provider"],recommended["model"],base_route.tier,
                f"cost-aware {routing_profile}: lowest sufficient eligible escalation level {recommended['level']}",
                recommended["cost_adjusted_score"],base_route.independence,tier_source=recommended["tier_source"],
                selected_by_policy=base_route.selected_by_policy,historical_recommendation=base_route.historical_recommendation)
        else: actual=base_route
        cost_display="unknown" if not recommended or recommended["expected_cost"] is None else f"{recommended['expected_cost']:.4f}"
        reason=("No discovered candidate met capability, quality, escalation and budget gates" if not recommended
                else f"{recommended['level']} satisfies {profile.complexity}/{profile.risk}/{profile.scope} with expected quality {recommended['expected_quality']:.2f} ({recommended['quality_source']}); cost {cost_display}")
        return {"task_profile":asdict(profile),"routing_profile":routing_profile,"mode":mode,"selected_by_policy":f"{base_route.provider}/{base_route.model or 'CLI default'}",
            "cost_aware_recommendation":key(recommended),"recommended_escalation_level":recommended["level"] if recommended else None,
            "estimated_savings":savings,"estimated_quality_delta":quality_delta,"marginal_quality_gain":gain,"marginal_cost":marginal_cost,
            "reason":reason,"candidates":rows,"actual_route":actual,"astra_escalation_reason":why_astra,
            "previous_models_attempted":[f"{item.get('provider')}/{item.get('model')}" for item in failures],
            "previous_failure_reasons":[item.get("category") for item in failures]}
