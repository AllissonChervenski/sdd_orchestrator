from dataclasses import dataclass, field
from typing import Any
import re
import random
from orchestrator.agents.history import classify_task, confidence, summarize


TIERS = ("fast", "balanced", "strong", "coding_strong")
TIER_TERMS = {
    "fast": {"flash": 5, "fast": 5, "mini": 4, "luna": 4, "low": 3, "haiku": 3, "high": -2, "opus": -3, "astra": -3, "ultra": -3, "max": -3},
    "balanced": {"medium": 5, "balanced": 5, "sonnet": 4, "sol": 3, "flash": 2, "opus": -2, "astra": -2, "ultra": -2, "max": -2},
    "strong": {"frontier": 6, "opus": 5, "astra": 5, "pro": 4, "ultra": 4, "max": 4, "high": 3, "medium": -2, "flash": -2, "luna": -2, "low": -3},
    "coding_strong": {"coding": 6, "code": 6, "coder": 6, "sol": 4, "sonnet": 4, "pro": 3, "astra": 2, "opus": 2, "flash": -2, "luna": -2, "low": -3},
}


@dataclass
class Route:
    provider: str
    model: str | None
    tier: str
    reason: str
    score: float = 0.0
    independence: bool | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    tier_source: str = "unknown"
    fallback_chain: list[str] = field(default_factory=list)
    selection_mode: str = "exploitation"
    selected_by_policy: str | None = None
    historical_recommendation: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def inferred_capabilities(provider: str, cap: Any) -> set[str]:
    result=set(getattr(cap,"capabilities",[]) or [])
    result.update({"REASONING","DOCUMENT_GENERATION","VALIDATION","CODING","AGENTIC_CODING"})
    if getattr(cap,"supports_file_editing",False) or provider in {"agy","opencode","codex"}: result.add("FILE_EDITING")
    if getattr(cap,"supports_shell",False) or provider in {"opencode","codex"}: result.add("SHELL")
    if getattr(cap,"supports_json",False) or provider in {"opencode","codex"}: result.add("STRUCTURED_OUTPUT")
    if getattr(cap,"metadata",{}).get("large_context"): result.add("LARGE_CONTEXT")
    if any(any(term in model.lower() for term in ("flash","fast","mini","luna","low")) for model in getattr(cap,"models",[])):
        result.add("FAST")
    return result


def choose_model(models: list[str], tier: str, configured: str | None = None, model_selection=True) -> tuple[str | None,str,float]:
    if configured:
        if not models or configured in models: return configured,"configured tier mapping",12.0
        return None,f"configured model {configured!r} was not discovered; using CLI default",0.0
    if not models or not model_selection: return None,"using CLI default; no explicit model available",0.0
    weights=TIER_TERMS[tier]
    choices=[]
    for name in models:
        # Provider prefixes such as "opencode-go" must not look like coding evidence.
        label=name.rsplit("/",1)[-1].lower()
        tokens=set(re.split(r"[-_.]+",label))
        score=sum(weight for term,weight in weights.items() if term in tokens)
        choices.append((score,name))
    score,name=max(choices,key=lambda pair:(pair[0],pair[1]))
    return name,f"tier-name heuristic fit {score}; explicit low-confidence selection" if score<3 else f"tier-name heuristic fit {score}",min(12.0,float(score))


def _detail(cap, model_id):
    return next((detail for detail in getattr(cap,"model_details",[]) if (detail.get("model_id") if isinstance(detail,dict) else detail.model_id)==model_id),None)


def _field(detail, name, default=None):
    return detail.get(name,default) if isinstance(detail,dict) else getattr(detail,name,default)


def _metadata_tiers(detail):
    if not detail: return set()
    meta=_field(detail,"metadata",{}) or {}
    explicit=meta.get("tiers")
    if explicit: return {str(t).lower() for t in explicit}
    # Only narrative CLI metadata is evidence here. Display/model names use the heuristic tier.
    label=str(meta.get("description") or "").lower()
    tiers=set()
    if any(word in label for word in ("flash","fast","affordable","efficient","lightweight")): tiers.add("fast")
    if any(word in label for word in ("balanced","everyday","workhorse","straightforward")): tiers.add("balanced")
    if any(word in label for word in ("frontier","demanding","complex reasoning","most capable")): tiers.add("strong")
    if any(word in label for word in ("coding","code generation","agentic")): tiers.add("coding_strong")
    return tiers


def describe_model(cap, provider, model_id, model_config=None, metrics=None):
    entry=(model_config or {}).get("models",{}).get(provider,{}).get(model_id,{})
    configured=[str(t).lower() for t in entry.get("tiers",[])]
    if configured: return configured,entry.get("tier_source","config")
    metadata=sorted(_metadata_tiers(_detail(cap,model_id)))
    if metadata: return metadata,"provider_metadata"
    history=(metrics or {}).get((provider,model_id),{})
    if history.get("samples",0)>=3 and history.get("success_rate",0)>=0.7: return ["balanced"],"history"
    scores={tier:choose_model([model_id],tier)[2] for tier in TIERS}
    best=max(scores.values())
    if best<=0: return ["unknown"],"heuristic"
    return [tier for tier,score in scores.items() if score==best],"heuristic"


def select_model(cap, provider, tier, model_config, legacy_mapping=None, metrics=None, required=(), estimated_context_tokens=None, only_model=None):
    """Return an explicit discovered model and the evidence behind its tier fit."""
    models=list(getattr(cap,"models",[]) or [])
    if only_model is not None: models=[model for model in models if model==only_model]
    if model_config.get("use_cli_default") or model_config.get("providers",{}).get(provider,{}).get("use_cli_default"):
        return None,"cli_default","configured use_cli_default: true",0.0
    if not models or not getattr(cap,"supports_model_selection",False):
        reason="model catalog unavailable" if not models else "CLI does not support explicit model selection"
        return None,"cli_default",reason,0.0
    if isinstance(legacy_mapping,dict): legacy_mapping=legacy_mapping.get("model")
    if legacy_mapping and legacy_mapping in models:
        return legacy_mapping,"config","legacy provider model_tiers mapping",12.0
    provider_models=model_config.get("models",{}).get(provider,{})
    if isinstance(provider_models,list): provider_models={m.get("model_id"):m for m in provider_models if isinstance(m,dict)}
    if provider_models.get("use_cli_default"): return None,"cli_default","configured use_cli_default: true",0.0
    candidates=[]
    for model_id in models:
        entry=provider_models.get(model_id,{}) or {}
        if entry.get("available") is False: continue
        if entry.get("use_cli_default"): continue
        detail=_detail(cap,model_id)
        if detail and _field(detail,"available") is False: continue
        context_window=_field(detail,"context_window") if detail else None
        if estimated_context_tokens and context_window and context_window < estimated_context_tokens: continue
        model_fields={"REASONING":"supports_reasoning","CODING":"supports_coding","FILE_EDITING":"supports_file_editing","SHELL":"supports_shell","AGENTIC_CODING":"supports_agentic_work","STRUCTURED_OUTPUT":"supports_structured_output"}
        if any(_field(detail,model_fields[capability]) is False for capability in required if capability in model_fields): continue
        tiers={str(t).lower() for t in entry.get("tiers",[])}
        if tier in tiers:
            source=entry.get("tier_source","config")
            fit=12.0
        elif tier in _metadata_tiers(detail):
            source="provider_metadata"; fit=9.0
        else:
            history=(metrics or {}).get((provider,model_id),{})
            if history.get("samples",0)>=3 and history.get("success_rate",0)>=0.7:
                source="history"; fit=6.0
            else:
                _,_,heuristic=choose_model([model_id],tier)
                source="heuristic"; fit=heuristic
        history=(metrics or {}).get((provider,model_id),{})
        rank={"config":4,"provider_metadata":3,"history":2,"heuristic":1}.get(source,1)
        score=rank*100+fit+max(-2.0,min(2.0,(float(history.get("success_rate",0.5))-0.5)*4.0))-min(4.0,float(history.get("recent_failures",0)))
        if tier=="fast":
            meta=_field(detail,"metadata",{}) or {}
            costs=[meta.get(key) for key in ("input_cost","output_cost")]
            if all(isinstance(value,(int,float)) for value in costs): score-=min(3.0,sum(costs)/10.0)
            if _field(detail,"speed_class")=="fast": score+=2.0
            latency=history.get("average_latency")
            if isinstance(latency,(int,float)): score-=min(2.0,latency/60.0)
        candidates.append((score,fit,model_id,source))
    if not candidates: return None,"unavailable","no available discovered model satisfies configuration/capabilities",0.0
    score,fit,model_id,source=max(candidates,key=lambda x:(x[0],x[1],x[2]))
    return model_id,source,f"{source} tier evidence for {tier}",fit


class ModelRouter:
    def __init__(self, capabilities: dict[str,Any], config: dict[str,Any] | None=None, metrics: dict[Any,Any] | None=None, history: list[dict] | None=None, rng=None):
        self.capabilities=capabilities
        self.config=config or {}
        self.metrics=metrics or {}
        self.history=history or []
        self.rng=rng or random.Random()

    def route(self, role: str, tier: str | None=None, exclude: set[str] | None=None, override_provider: str | None=None, override_model: str | None=None, author_provider: str | None=None, task: dict | None=None, high_risk=False, blocked=False, author_models=None, author_role=None, numeric_sensitive: bool = False) -> Route:
        from orchestrator.agents.roles import ROLES, ROLE_ALIASES
        from orchestrator.agents.independence import is_family_independent
        canonical_role = ROLE_ALIASES.get(role, role)
        spec=ROLES.get(canonical_role) or ROLES.get(role)
        role_cfg=self.config.get("roles",{}).get(canonical_role) or self.config.get("roles",{}).get(role,{})
        tier=(tier or role_cfg.get("tier") or (spec.tier if spec else "balanced")).lower()
        if tier not in TIERS: tier="balanced"
        required=set(role_cfg.get("required_capabilities",spec.required_capabilities if spec else ()))
        preferred_caps=set(role_cfg.get("preferred_capabilities",spec.preferred_capabilities if spec else ()))
        role_preference=role_cfg.get("preferred_providers",spec.preferred_providers if spec else ())
        if not role_preference: role_preference=self.config.get("provider_preference",["agy","opencode","codex"])
        if override_provider: role_preference=[override_provider,*[p for p in role_preference if p!=override_provider]]
        explicit_excludes=set(exclude or ())
        candidates=[]
        for provider,cap in self.capabilities.items():
            if not getattr(cap,"cli_available",False) or provider in explicit_excludes: continue
            actual=inferred_capabilities(provider,cap)
            if required.issubset(actual): candidates.append((provider,cap,actual))
        if not candidates: return Route("unavailable",None,tier,f"No available provider satisfies required capabilities: {', '.join(sorted(required))}")
        if override_provider:
            forced=[entry for entry in candidates if entry[0]==override_provider]
            if forced: candidates=forced

        independent_candidates=[entry for entry in candidates if entry[0]!=author_provider] if author_provider else candidates
        independence=True if author_provider and independent_candidates else (False if author_provider else None)
        # Enforce provider separation when at least one compatible alternative exists.
        if independent_candidates: candidates=independent_candidates
        pref_index={p:i for i,p in enumerate(role_preference)}
        global_pref=self.config.get("provider_preference",["agy","opencode","codex"])
        global_index={p:i for i,p in enumerate(global_pref)}
        routing=self.config.get("routing",{})
        mode=routing.get("adaptive_routing_mode","observe")
        task_type,complexity=classify_task(canonical_role,task)
        is_numeric = numeric_sensitive or bool((task or {}).get("numeric_sensitive", False))
        preferred_models: tuple[str, ...]
        if is_numeric and canonical_role == "test_designer":
            preferred_models = ("gpt-6-sol", "claude-sonnet-4-6")
        elif is_numeric and canonical_role == "coder":
            preferred_models = ("claude-sonnet-4-6", "gpt-6-sol", "opencode-go/kimi-k3")
        elif is_numeric and canonical_role == "test_validator":
            preferred_models = ("opencode-go/mimo-v2.6-pro", "opencode-go/kimi-k3", "gemini-3.8-flash-high")
        elif is_numeric and canonical_role == "refactorer":
            preferred_models = ("opencode-go/kimi-k3",)
        elif is_numeric and canonical_role == "code_reviewer":
            preferred_models = ("claude-sonnet-4-6", "gemini-3.8-flash-high", "opencode-go/mimo-v2.6-pro")
        elif complexity=="HIGH" and (role_cfg.get("complex_preferred_models") or (spec and spec.complex_preferred_models)):
            preferred_models=tuple(role_cfg.get("complex_preferred_models") or (spec.complex_preferred_models if spec else ()))
        else:
            preferred_models=tuple(role_cfg.get("preferred_models") or (spec.preferred_models if spec else ()))
        model_pref_index={m:i for i,m in enumerate(preferred_models)}
        minimum=max(1,int(routing.get("historical_min_samples",10)))
        scored=[]
        for provider,cap,actual in candidates:
            mapping=self.config.get("model_tiers",{}).get(tier,{}).get(provider)
            model_options=[override_model] if override_model else (list(cap.models) if cap.models and cap.supports_model_selection else [None])
            for candidate_model in model_options:
                model,tier_source,model_reason,model_fit=select_model(cap,provider,tier,self.config.get("models_config",{}),mapping,self.metrics,required,role_cfg.get("estimated_context_tokens"),candidate_model)
                if tier_source=="unavailable": continue
                if override_model:
                    if getattr(cap,"models",[]) and override_model not in cap.models: continue
                    model,tier_source,model_reason,model_fit=override_model,"config","manual model override",12.0
                if model is None and candidate_model is not None and tier_source!="cli_default": continue
                if author_models:
                    ok_ind, _ = is_family_independent(author_models, model, author_role=author_role, validator_role=canonical_role, numeric_sensitive=is_numeric)
                    if not ok_ind: continue
                idx=pref_index.get(provider,len(pref_index)+1)
                aggregate=self.metrics.get((provider,model),{}) or {}
                failures=float(aggregate.get("recent_failures",0))
                m_idx=model_pref_index.get(model)
                role_model_pref=max(0.0,60.0-12.0*m_idx) if m_idx is not None else (0.0 if preferred_models else 0.0)
                breakdown={
                    "required_capabilities":100.0,
                    "preferred_capabilities":5.0*len(preferred_caps & actual),
                    "role_provider_preference":max(0.0,80.0-18.0*idx),
                    "global_provider_preference":max(0.0,4.0-2.0*global_index.get(provider,len(global_index))),
                    "role_model_preference":role_model_pref,
                    "independence":16.0 if author_provider and provider!=author_provider else (-32.0 if author_provider else 0.0),
                    "model_tier_fit":model_fit + {"config":24,"provider_metadata":15,"history":7}.get(tier_source,0),
                    "explicit_model":-150.0 if model is None else 0.0,
                    "recent_failures":-min(20.0,failures*4.0),
                }
                if model:
                    lowered=model.lower()
                    if "astra" in lowered and not override_model:
                        breakdown["extreme_fallback"]=-100.0
                    if "opus" in lowered and not override_model:
                        breakdown["extraordinary_fallback"]=-150.0
                base=sum(breakdown.values())
                matched=[row for row in self.history if row.get("provider")==provider and row.get("model")==model and row.get("role") in {canonical_role, role}]
                contextual=[row for row in matched if row.get("task_type")==task_type and row.get("task_complexity")==complexity]
                chosen=contextual if len(contextual)>=minimum else matched
                stats=summarize(chosen,float(routing.get("decay_half_life_days",30)))
                conf=confidence(stats.get("effective_samples",0),minimum)
                historical=float(stats["historical_score"])
                influence=0.0 if mode=="observe" else (0.5 if mode=="assist" else 1.0)
                total=base+historical*conf*influence
                model_pref_text=f"model preference #{m_idx+1} ({model}); " if m_idx is not None else ""
                reason=(f"capabilities {', '.join(sorted(required))} matched; role preference #{idx+1}; {model_pref_text}"
                        f"{model_reason}; base={base:.1f}, historical={historical:.1f}, confidence={conf:.2f}, mode={mode}; score={total:.1f}")
                if author_provider:
                    reason+=f"; {'different from' if provider!=author_provider else 'same as'} author provider {author_provider}"
                    if not independence: reason+=" (no other compatible provider; self-validation permitted)"
                model_cfg_entry=(self.config.get("models_config",{}) or {}).get("models",{}).get(provider,{}).get(model,{})
                configured_model_caps=[str(c).upper() for c in model_cfg_entry.get("capabilities",[])]
                candidate_caps=sorted(actual | set(configured_model_caps))
                scored.append({"final_score":total,"base_score":base,"provider":provider,"model":model,"reason":reason,"score_breakdown":breakdown,"tier_source":tier_source,"tier":tier,"capabilities":candidate_caps,"historical_score":historical,"historical_confidence":conf,"historical_metrics":stats,"penalties":{key:value for key,value in breakdown.items() if value<0}})
        if not scored and author_provider and not override_provider and not author_models:
            fallback=self.route(role,tier=tier,exclude=exclude,override_model=override_model,task=task,high_risk=high_risk,blocked=blocked)
            if fallback.provider!="unavailable":
                fallback.independence=False
                fallback.reason+="; no independent provider/model is compatible; self-validation permitted"
            return fallback
        if not scored:
            if author_models:
                return Route("unavailable", None, tier, "INSUFFICIENT_INDEPENDENT_PROVIDERS: all candidate models collide in family with author")
            return Route("unavailable",None,tier,"No provider/model matched the explicit override")
        scored.sort(key=lambda item:(-item["final_score"],item["provider"],item["model"] or ""))
        policy=sorted(scored,key=lambda item:(-item["base_score"],item["provider"],item["model"] or ""))[0]
        historical_item=sorted(scored,key=lambda item:(-(item["base_score"]+item["historical_score"]*item["historical_confidence"]),item["provider"],item["model"] or ""))[0]
        selected=policy if mode=="observe" else scored[0]
        selection_mode="exploitation"
        rate=float(routing.get("exploration_rate",0.05))
        risky=high_risk or (task or {}).get("risk") in {"HIGH","CRITICAL"}
        task_blocked=blocked or (task or {}).get("status")=="BLOCKED"
        if mode!="observe" and rate>0 and role!="constitution" and not risky and not task_blocked and not override_model and not override_provider and len({item["provider"] for item in scored})>1:
            alternatives=[item for item in scored if item is not selected and item["base_score"]>=policy["base_score"]-20]
            if alternatives and self.rng.random()<rate:
                selected=self.rng.choice(alternatives); selection_mode="exploration"
        fallback_chain=[f"{item['provider']}/{item['model'] or 'CLI default'}" for item in scored if item is not selected]
        return Route(selected["provider"],selected["model"],tier,selected["reason"],selected["final_score"],independence,selected["score_breakdown"],selected["tier_source"],fallback_chain,selection_mode,f"{policy['provider']}/{policy['model'] or 'CLI default'}",f"{historical_item['provider']}/{historical_item['model'] or 'CLI default'}",scored)

    def explain(self, role, task=None):
        from orchestrator.agents.roles import ROLE_ALIASES
        canonical_role = ROLE_ALIASES.get(role, role)
        route=self.route(role,task=task)
        result={"role":role,"task_type":classify_task(canonical_role,task)[0],"task_complexity":classify_task(canonical_role,task)[1],"selected":f"{route.provider}/{route.model or 'CLI default'}","selected_by_policy":route.selected_by_policy,"historical_recommendation":route.historical_recommendation,"selection_mode":route.selection_mode,"candidates":route.candidates}
        if canonical_role != role:
            result["alias_for"] = canonical_role
        return result

    def independent_route(self, role: str, author_provider: str, tier: str | None=None) -> tuple[Route,bool]:
        route=self.route(role,tier=tier,author_provider=author_provider)
        return route,bool(route.independence)
