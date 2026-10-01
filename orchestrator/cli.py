import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from orchestrator.config.loader import load_config
from orchestrator.config.models import Config, ModelCapabilities, ProviderCapabilities
from orchestrator.doctor import discover_all, write_selection_report
from orchestrator.agents.router import ModelRouter, describe_model
from orchestrator.agents.plan import build_route_plan, format_route
from orchestrator.providers import PROVIDERS
from orchestrator.storage.sqlite import StateStore
from orchestrator.verification.harness import VerificationHarness
from orchestrator.agents.history import summarize, confidence
from orchestrator.agents.cost import CostAwareRouter, TaskProfile
from orchestrator.agents.execution_policy import ExecutionPolicyRouter


def _load_provider_capabilities(root, data=None):
    if data is None:
        path = root / ".orchestrator" / "capabilities.json"
        if path.exists():
            try: data=json.loads(path.read_text())
            except ValueError: data={}
        else: data={}
    caps={}
    for name in PROVIDERS:
        if name in data:
            item=dict(data[name])
            item["model_details"]=[ModelCapabilities(**detail) for detail in item.get("model_details",[]) if isinstance(detail,dict)]
            caps[name]=ProviderCapabilities(**item)
        else:
            caps[name]=ProviderCapabilities(provider=name, metadata={"discovery":"not run; use `python -m orchestrator doctor`"})
    return caps


def _router_config(cfg):
    return {"roles":cfg.roles, "model_tiers":cfg.providers.get("model_tiers",{}), "models_config":cfg.models,
            "provider_preference":cfg.providers.get("preference", ["codex","opencode","agy"]), "routing":cfg.routing}


def _build_router(cfg, caps, db_path):
    metrics=StateStore.load_provider_metrics(db_path)
    history=StateStore(db_path).routing_history() if db_path.exists() else []
    return ModelRouter(caps, _router_config(cfg), metrics, history)


def _router(root, cfg):
    caps=_load_provider_capabilities(root)
    db_path=root/".orchestrator"/"state"/"orchestrator.sqlite3"
    return _build_router(cfg, caps, db_path), caps


_CAPABILITY_FLAG_FIELDS=("supports_coding","supports_reasoning","supports_structured_output","supports_file_editing","supports_shell","supports_agentic_work")


def _capability_names(detail, cfg_caps):
    known=[] if not detail else [field.removeprefix("supports_") for field in _CAPABILITY_FLAG_FIELDS if getattr(detail,field) is True]
    return list(dict.fromkeys(known + cfg_caps))


def doctor(args):
    root=Path.cwd(); cfg=load_config(args.config)
    caps=discover_all(root)
    db_path=root/".orchestrator"/"state"/"orchestrator.sqlite3"
    router=_build_router(cfg,caps,db_path)
    write_selection_report(root,caps,router)
    print(f"Python .......... OK ({sys.version.split()[0]})")
    print(f"Git ............. {'OK' if shutil.which('git') else 'UNAVAILABLE'}")
    print("\nProviders:")
    for name, cap in caps.items(): print(f"{name.title():<16} {'OK' if cap.cli_available else 'UNAVAILABLE'} {cap.cli_version or ''}")
    print("\nModels:")
    for name, cap in caps.items():
        print(f"  {name}: {len(cap.models)} discovered")
        for model_id in cap.models:
            tiers,source=describe_model(cap,name,model_id,cfg.models)
            detail=next((item for item in cap.model_details if item.model_id==model_id),None)
            cfg_entry=(cfg.models or {}).get("models",{}).get(name,{}).get(model_id,{})
            all_caps=_capability_names(detail,cfg_entry.get("capabilities",[]))
            print(f"    {model_id}: {','.join(tiers)} tier_source={source}; model_capabilities={','.join(all_caps) or 'unknown'}")
        if not cap.models: print(f"  {name}: not detected ({cap.metadata.get('model_discovery_error') or 'no catalog returned'})")
    print("\nVerification:")
    harness=VerificationHarness(root,cfg.verification)
    detected=harness.detect()
    warnings=[]
    for name,cap in caps.items():
        if cap.metadata.get("model_discovery"):
            warnings.append(f"{name} catalog stale: {cap.metadata['model_discovery']}; {cap.metadata.get('model_discovery_error') or 'probe returned no models'}")
    labels={"lint":"lint", "type":"type checking", "syntax":"syntax check", "static":"static (legacy)"}
    for kind in ("build", "task_tests", "regression_tests", "tests", "lint", "type", "syntax", "static", "requirements"):
        if kind=="task_tests":
            print("  task_tests       dynamic (TestDesigner per task)")
            continue
        commands=cfg.verification.get(kind) or (cfg.verification.get("tests") if kind=="regression_tests" else None)
        if kind=="static" and not commands and cfg.verification.get("type"): continue
        state="configured" if commands else ("suggested" if detected.get(kind) else "not configured")
        print(f"  {labels.get(kind,kind):<16} {state}")
        if not commands: warnings.append(f"missing verification gate: {kind}")
    for module,kind in (("ruff","lint"),("mypy","type")):
        if cfg.verification.get(kind) and importlib.util.find_spec(module) is None:
            warnings.append(f"{kind} configured but {module} is not installed in this Python environment")
    plan=build_route_plan(router,["constitution","constitution_validator","specification","specification_validator","planning","plan_validator","tasks","tasks_validator","consistency_agent","test_designer","test_validator","coder","code_reviewer","final_reviewer"])
    for role,route in plan.items():
        if route.tier_source=="cli_default": warnings.append(f"{role} uses CLI default: {route.reason}")
        elif route.tier_source=="heuristic": warnings.append(f"{role} uses heuristic tier: {route.provider}/{route.model}")
    print("\nWarnings:")
    for warning in dict.fromkeys(warnings): print("  -",warning)
    if not warnings: print("  none")
    print("Capabilities saved to .orchestrator/capabilities.json")
    if getattr(args,"live",False):
        from orchestrator.doctor import live_smoke_tests
        print("\nLive provider smoke tests (explicitly requested):")
        for name,result in live_smoke_tests(root,caps,cfg.models).items():
            print(f"  {name}: provider={result.provider} structured_output={result.structured_output}")
            for phase in ("basic_smoke","structured_smoke"):
                check=getattr(result,phase)
                print(f"    {phase}: {check.status}")
                if getattr(args,"verbose",False):
                    print(f"      execution: {json.dumps(check.execution)}")
                    print(f"      exit_code: {check.exit_code}")
                    print(f"      parsing: {check.parsing}")
                    print(f"      contract: {check.contract}")
                    print(f"      failure_layer: {check.failure_layer or 'none'}")
                    print(f"      failure_category: {check.failure_category or 'none'}")
                    if check.error: print(f"      error: {check.error}")
                    if check.traceback: print(f"      traceback:\n{check.traceback}")


def models(args):
    root=Path.cwd(); cfg=load_config(args.config); router,caps=_router(root,cfg); write_selection_report(root,caps,router)
    history=StateStore.load_model_metrics(root/".orchestrator"/"state"/"orchestrator.sqlite3")
    print("Provider | Model | Available | Tiers | Tier source | Capabilities | Historical success | Average retries | Average latency")
    def fmt(value): return "unknown" if value is None else f"{value:.2f}" if isinstance(value,float) else str(value)
    for name,cap in caps.items():
        if not cap.models: print(f"{name} | CLI default | {cap.cli_available} | unknown | cli_default | {','.join(cap.capabilities) or 'unknown'} | unknown | unknown | unknown")
        for model_id in cap.models:
            tiers,source=describe_model(cap,name,model_id,cfg.models,history)
            metric=history.get((name,model_id),{})
            detail=next((item for item in cap.model_details if item.model_id==model_id),None)
            cfg_entry=(cfg.models or {}).get("models",{}).get(name,{}).get(model_id,{})
            all_caps=_capability_names(detail,cfg_entry.get("capabilities",[]))
            available="unknown" if detail is None else str(detail.available).lower()
            print(f"{name} | {model_id} | {available} | {','.join(tiers)} | {source} | {','.join(all_caps) or 'unknown'} | {fmt(metric.get('success_rate'))} | {fmt(metric.get('average_retries'))} | {fmt(metric.get('average_latency'))}")
    print("Selection report: .orchestrator/model-selection.md")


@dataclass(frozen=True)
class ProviderSummaryItem:
    name: str
    available: bool
    model_count: int


@dataclass(frozen=True)
class ProviderSummaryReport:
    providers: list[ProviderSummaryItem]

    def to_dict(self) -> dict:
        return {"providers": [asdict(item) for item in self.providers]}


def _build_provider_summary() -> ProviderSummaryReport:
    """Build a provider summary from the local registry and capability cache.

    Uses PROVIDERS registry membership to determine known providers and the
    cached ProviderCapabilities loaded by _load_provider_capabilities. No
    adapter discovery, probes, subprocesses, state-store access, or LLM calls
    are performed.
    """
    root = Path.cwd()
    path = root / ".orchestrator" / "capabilities.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object in capabilities cache")
    caps = _load_provider_capabilities(root, data=data)
    items = []
    for name in PROVIDERS:
        cap = caps.get(name)
        available = cap.cli_available is True if cap else False
        catalog = getattr(cap, "models", None) if cap else None
        model_count = len(catalog) if isinstance(catalog, list) else 0
        items.append(ProviderSummaryItem(name=name, available=available, model_count=model_count))
    return ProviderSummaryReport(providers=items)


def provider_summary(args):
    try:
        report = _build_provider_summary()
    except (OSError, ValueError, TypeError) as exc:
        print(f"provider-summary: invalid or unreadable capabilities cache: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(report.to_dict()))
        return
    for item in report.providers:
        status = "OK" if item.available else "UNAVAILABLE"
        print(f"{item.name} | {status} | {item.model_count} discovered")


def run(args):
    root=Path.cwd(); cfg=load_config(args.config); router,caps=_router(root,cfg)
    if getattr(args,"first_real_run",False):
        cfg.real_run["safety_mode"]="strict"
        cfg.routing["adaptive_routing_mode"]="observe"
        router.config["routing"]={**cfg.routing}
        cfg.cost_optimization["mode"]="observe"
        args.interactive=True
    feature=args.feature or (Path(args.feature_file).read_text() if args.feature_file else "")
    if not feature.strip(): raise SystemExit("Provide --feature or --feature-file")
    harness=VerificationHarness(root,cfg.verification)
    roles=["constitution","constitution_validator","specification","specification_validator","planning","plan_validator","tasks","tasks_validator","consistency_agent","test_designer","test_validator","coder","refactorer","code_reviewer","final_reviewer"]
    print("Workflow: Constitution → Spec → Clarify → Checklist → Plan → Tasks → Analysis (speckit-analyze) → TDD (Red/Green/Refactor/Review) → Converge → Final verification")
    print("Roles:", ", ".join(roles))
    route_plan=build_route_plan(router,roles,{"coder":getattr(args,"coder_provider",None)} if getattr(args,"coder_provider",None) else {})
    cost_router=CostAwareRouter(router,cfg.cost_optimization)
    policy_router=ExecutionPolicyRouter(cfg.execution_policies,caps)
    ladder=[item for item in cfg.cost_optimization.get("ladder",[]) if caps.get(item.get("provider")) and item.get("model") in caps[item["provider"]].models]
    ladder_text=" → ".join(f"{item['level']} ({item['provider']}/{item['model']})" for item in ladder) or "no configured models discovered"
    for role in roles:
        route=route_plan[role]
        print("  "+format_route(role,route))
        cost=cost_router.assess(role,route)
        policy=policy_router.select(role,route.provider,TaskProfile.derive(role))
        print(f"    task_profile: {cost['task_profile']['task_type']}/{cost['task_profile']['complexity']}/{cost['task_profile']['risk']}/{cost['task_profile']['scope']}; routing_profile={cost['routing_profile']}")
        print(f"    cost_aware_recommendation: {cost['cost_aware_recommendation'] or 'none'}; preferred_starting_level: {cost['recommended_escalation_level'] or 'unknown'}; mode={cost['mode']}; estimated_savings={cost['estimated_savings'] if cost['estimated_savings'] is not None else 'unknown'}")
        ponytail_source=policy.policy_source if policy.ponytail_enabled else "off"
        caveman_source=policy.policy_source if policy.caveman_enabled else "off"
        print(f"    ponytail: {policy.ponytail_mode} ({ponytail_source}); caveman: {policy.caveman_mode} ({caveman_source}); policy_overhead_estimate={policy.policy_overhead_estimate} tokens")
        if cost["cost_aware_recommendation"]!=cost["selected_by_policy"]: print(f"    policy_difference: selected_by_policy={cost['selected_by_policy']}; recommendation={cost['reason']}")
        if route.model and any(item.get("level")=="ASTRA" and item.get("provider")==route.provider and item.get("model")==route.model for item in cfg.cost_optimization.get("ladder",[])):
            print(f"    WHY_ASTRA: {cost['astra_escalation_reason'] or 'missing; real agent call blocked until justified'}")
    print("Escalation ladder:",ladder_text)
    print("Extraordinary fallback:",json.dumps(cfg.cost_optimization.get("extraordinary_fallback",{})))
    print("Validation gates: artifacts; cross-artifact; test validation; RED expected failure; GREEN; regression; review; deterministic final verification")
    print("Task tests: selected from TestDesigner.created_tests and test_commands per task")
    detected=harness.detect()
    commands={key:cfg.verification.get(key,[]) for key in ("task_tests","regression_tests","build","tests","lint","type","static","syntax","requirements")}
    print("Configured verification commands:", json.dumps(commands))
    print("Suggested verification commands (review with `configure`):", json.dumps(detected))
    if any(route.tier_source=="cli_default" for route in route_plan.values()): print("WARNING: model not explicitly resolved for one or more roles")
    if args.dry_run: return
    from orchestrator.git.repository import GitRepository
    repository=GitRepository(root)
    if cfg.real_run.get("safety_mode")=="strict" and not repository.available():
        raise SystemExit("Strict real run requires a Git repository")
    if repository.available() and repository.dirty():
        raise SystemExit("Refusing to run agents: Git working tree has uncommitted changes. Commit or stash them before execution.")
    store=StateStore(root/".orchestrator"/"state"/"orchestrator.sqlite3")
    wid=store.create_workflow(feature, {"stage":"PLANNED","workspace":str(root.resolve()),"first_real_run":bool(getattr(args,"first_real_run",False)),"attempts":{},"completed_tasks":[],"blocked_tasks":[],"validation_results":[],"verification_results":[],"state_schema_version":store.SCHEMA_VERSION})
    print(f"Recommended dedicated branch: orchestrator/{wid}; current branch: {repository.branch() or 'unknown'}")
    if getattr(args,"worktree",False):
        import tempfile
        root=repository.create_worktree(Path(tempfile.gettempdir())/"orchestrator-worktrees"/wid,f"orchestrator/{wid}")
        repository=GitRepository(root)
        item=store.get_workflow(wid)
        store.update_workflow(wid,"PLANNED",{**item["state"],"workspace":str(root),"worktree":True})
        harness=VerificationHarness(root,cfg.verification)
    run_dir=root/".orchestrator"/"runs"/wid
    for d in ("state","logs","runs","reports"): (root/".orchestrator"/d).mkdir(parents=True,exist_ok=True)
    (run_dir).mkdir(parents=True,exist_ok=True)
    from orchestrator.workflow.resume import WorkspaceFingerprint
    store.create_checkpoint(wid,"WORKFLOW_PLANNED:-:1","WORKFLOW_PLANNED",WorkspaceFingerprint(root).capture(wid))
    from orchestrator.agents.runner import AgentRunner
    from orchestrator.workflow.driver import run_sdd_workflow, WorkflowBlocked
    provider_instances={name:cls() for name,cls in PROVIDERS.items()}
    if "opencode" in provider_instances:
        provider_instances["opencode"].agent_map=cfg.providers.get("opencode",{}).get("agent_map",{})
    # Refresh per-adapter discovered flags used to safely construct commands.
    for provider in provider_instances.values(): provider.discover()
    runner=AgentRunner(provider_instances,router,store,workflow_id=wid,safety=cfg.real_run,
        cost_router=CostAwareRouter(router,cfg.cost_optimization,store,wid),execution_policy_router=policy_router)
    from orchestrator.interactive import InteractiveGate
    interactive_gate=InteractiveGate(workspace=root, store=store, workflow_id=wid) if getattr(args,"interactive",False) else None
    if interactive_gate:
        runner.on_fallback=lambda role,provider,model: interactive_gate.confirm("PROVIDER_FALLBACK",role,provider,model,[],[])
        if getattr(args,"first_real_run",False):
            runner.on_call=lambda role,provider,model,task_id,files,outputs,artifacts=(),attempt=1: interactive_gate.confirm("AGENT_CALL",role,provider,model,files,outputs,task_id=task_id,attempt=attempt,artifacts=artifacts)
            runner.on_call_complete=lambda result: interactive_gate.agent_call_completed(result)

    def command_event(event, payload):
        if event=="started":
            item=store.get_workflow(wid)
            if item: store.update_workflow(wid,"RUNNING_VERIFICATION",{**item["state"],"running_command":payload["command"]},item["current_task"])
        else: store.record_verification(wid,payload)
    harness.on_command=command_event
    def gate_callback(stage, role, files=(), commands=(), attempt=1, artifacts=(), **kwargs):
        if not interactive_gate: return True
        route=route_plan.get(role)
        return interactive_gate.confirm(stage,role,route.provider if route else "python",route.model if route else None,files,commands,attempt=attempt,artifacts=artifacts,extra_context=kwargs.get("extra_context"))

    def approve_constitution():
        if interactive_gate: return gate_callback("CONSTITUTION_CREATE","constitution",[".specify/memory/constitution.md"],[])
        if not sys.stdin.isatty(): return False
        return input("Create constitution.md? This is a human-gated project change. Type 'approve': ").strip()=="approve"
    try:
        with store.workflow_lock(wid):
            result=run_sdd_workflow(feature,root,runner,harness,cfg,run_dir,approve_constitution,store=store,gate_callback=gate_callback,
                                    clarification_callback=interactive_gate.ask_clarifications if interactive_gate else None)
        current=store.get_workflow(wid)
        prior=current["state"] if current else {}
        state={**prior,"stage":"COMPLETE","completed_tasks":result["completed_tasks"],"traceability":result["traceability"],"verification_results":result["final_verification"],"requirement_verification":result["requirement_verification"]}
        store.update_workflow(wid,"COMPLETE",state)
        (root/".orchestrator"/"reports"/f"{wid}.json").write_text(json.dumps(state,indent=2))
        print(f"Workflow complete: {wid}")
    except WorkflowBlocked as exc:
        stage="ABORTED" if "aborted" in str(exc).lower() else "BLOCKED"
        current=store.get_workflow(wid)
        state={**(current["state"] if current else {}),"stage":stage,"reason":str(exc)}
        store.update_workflow(wid,stage,state)
        (root/".orchestrator"/"reports"/f"{wid}.json").write_text(json.dumps(state,indent=2))
        print(f"Workflow BLOCKED: {exc}\nWorkflow ID: {wid}")


def status(args):
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
    print(json.dumps(store.get_workflow(args.workflow_id) if getattr(args,"workflow_id",None) else store.list_workflows(),indent=2))


def workflows(args):
    print(json.dumps(StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3").list_workflows(),indent=2))


def inspect(args):
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
    item=store.get_workflow(args.workflow_id)
    if not item: raise SystemExit("Workflow not found")
    print(json.dumps({
        "workflow": item,
        "checkpoints": store.checkpoints(args.workflow_id),
        "resume_reports": store.list_resume_reports(args.workflow_id),
        "validations": store.list_validations(args.workflow_id),
    }, indent=2))


def checkpoints(args):
    print(json.dumps(StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3").checkpoints(args.workflow_id),indent=2))


def abort(args):
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
    try:
        with store.workflow_lock(args.workflow_id): store.abort_workflow(args.workflow_id)
    except (ValueError,RuntimeError) as exc: raise SystemExit(str(exc)) from exc
    print(f"ABORTED {args.workflow_id}; workspace and evidence preserved")


def trace(args):
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
    print(json.dumps(store.list_traceability(args.requirement_id),indent=2))


def metrics(args):
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
    rows=store.routing_history(getattr(args,"role",None),getattr(args,"provider",None),getattr(args,"model",None))
    cfg=load_config(getattr(args,"config","orchestrator.yaml"))
    grouped={}
    for row in rows: grouped.setdefault((row["provider"],row["model"],row["role"]),[]).append(row)
    print("Provider/Model | Role | Runs | Success | First-pass | Retries | Latency | Review rejection | Regression failure | Confidence")
    for (provider,model,role),group in sorted(grouped.items(),key=lambda entry:(entry[0][0],entry[0][1] or "",entry[0][2])):
        stats=summarize(group,float(cfg.routing.get("decay_half_life_days",30)))
        conf=confidence(stats.get("effective_samples",0),int(cfg.routing.get("historical_min_samples",10)))
        print(f"{provider}/{model or 'CLI default'} | {role} | {stats['runs']} | {stats['success_rate']:.2f} | {stats['first_pass_success_rate']:.2f} | {stats['average_attempts']-1:.2f} | {stats['average_latency']:.2f}s | {stats['review_rejection_rate']:.2f} | {stats['regression_failure_rate']:.2f} | {conf:.2f}")
    if not grouped: print("No real execution history recorded")
    print("TDD:",json.dumps(store.summarize_metrics()["tdd"]))
    roi=store.cost_roi(int(cfg.routing.get("historical_min_samples",10)),getattr(args,"role",None))
    roi=[item for item in roi if (not getattr(args,"provider",None) or item["provider"]==args.provider) and (not getattr(args,"model",None) or item["model"]==args.model)]
    print("Cost ROI (contextual minimum samples):",json.dumps(roi))


def route_explain(args):
    cfg=load_config(args.config); router,_=_router(Path.cwd(),cfg)
    task={"id":args.task} if args.task else None
    source="role_default"
    if args.task:
        store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3")
        workflows=[store.get_workflow(args.workflow_id)] if getattr(args,"workflow_id",None) else [store.get_workflow(row["workflow_id"]) for row in store.list_workflows()]
        for workflow in workflows:
            if not workflow: continue
            root=Path(workflow["state"].get("workspace") or Path.cwd())
            path=root/".orchestrator"/"runs"/workflow["workflow_id"]/"tasks.md"
            if not path.is_file(): continue
            try: found=next((item for item in json.loads(path.read_text()).get("tasks",[]) if item.get("id")==args.task),None)
            except (ValueError,AttributeError): found=None
            if found: task=found; source=str(path); break
    report=router.explain(args.role,task); report["task_metadata_source"]=source if source!="role_default" else ("id_only" if args.task else "role_default")
    cost=CostAwareRouter(router,cfg.cost_optimization).assess(args.role,router.route(args.role,task=task),task,escalation_reason=getattr(args,"escalation_reason",None))
    report["cost_aware"]={key:value for key,value in cost.items() if key!="actual_route"}
    if args.json:
        print(json.dumps(report,indent=2)); return
    print(f"Role: {report['role']} | Task: {report['task_type']}/{report['task_complexity']} ({report['task_metadata_source']})")
    print(f"Selected: {report['selected']} | Policy: {report['selected_by_policy']} | Historical: {report['historical_recommendation']} | Mode: {report['selection_mode']}")
    print(f"Task profile: {cost['task_profile']['task_type']}/{cost['task_profile']['complexity']}/{cost['task_profile']['risk']}/{cost['task_profile']['scope']} | Routing profile: {cost['routing_profile']} | Cost mode: {cost['mode']}")
    print(f"Cost recommendation: {cost['cost_aware_recommendation'] or 'none'} | Level: {cost['recommended_escalation_level'] or 'none'} | Marginal quality: {cost['marginal_quality_gain'] if cost['marginal_quality_gain'] is not None else 'unknown'} | Marginal cost: {cost['marginal_cost'] if cost['marginal_cost'] is not None else 'unknown'}")
    print("Reason:",cost["reason"])
    print("Provider/Model | Tier | Base | History | Confidence | Penalties | Final | Capabilities")
    for candidate in report["candidates"]:
        penalties=",".join(f"{name}={value:.1f}" for name,value in candidate["penalties"].items()) or "none"
        print(f"{candidate['provider']}/{candidate['model'] or 'CLI default'} | {candidate['tier']} ({candidate['tier_source']}) | {candidate['base_score']:.1f} | {candidate['historical_score']:.1f} | {candidate['historical_confidence']:.2f} | {penalties} | {candidate['final_score']:.1f} | {','.join(candidate['capabilities'])}")
    print("Cost candidates: Provider/Model | Level | Capability fit | Quality (source) | Confidence | Expected cost | Retry risk | Base | Adjusted | Eligibility")
    for item in cost["candidates"]:
        print(f"{item['provider']}/{item['model']} | {item['level']} | {item['capability_fit']:.1f} | {item['expected_quality']:.2f} ({item['quality_source']}) | {item['historical_confidence']:.2f} | {item['expected_cost'] if item['expected_cost'] is not None else 'unknown'} | {item['retry_risk']:.2f} | {item['base_score']:.1f} | {item['cost_adjusted_score']:.1f} | {item['eligibility']}: {', '.join(item['eligibility_reasons']) or 'sufficient'}")
        if item["level"]=="ASTRA": print("  WHY_ASTRA:",item["WHY_ASTRA"] or "not eligible without audited escalation reason")
        if item["level"]=="OPUS": print("  WHY_OPUS:",item["WHY_OPUS"])


def resume(args):
    from orchestrator.workflow.resume import resume_workflow
    from orchestrator.workflow.driver import run_sdd_workflow, WorkflowBlocked
    from orchestrator.agents.runner import AgentRunner
    from orchestrator.interactive import InteractiveGate
    store=StateStore(Path.cwd()/".orchestrator"/"state"/"orchestrator.sqlite3"); item=store.get_workflow(args.workflow_id)
    if not item: raise SystemExit("Workflow not found")
    root=Path(item["state"].get("workspace") or Path.cwd()).resolve()
    cfg=load_config(args.config); router,_=_router(Path.cwd(),cfg)
    if item["state"].get("first_real_run"):
        cfg.real_run["safety_mode"]="strict"
        cfg.routing["adaptive_routing_mode"]="observe"
        router.config["routing"]={**cfg.routing}
        cfg.cost_optimization["mode"]="observe"
    harness=VerificationHarness(root,cfg.verification)
    gate=InteractiveGate(workspace=root, store=store, workflow_id=args.workflow_id) if args.interactive or item["state"].get("first_real_run") else None
    def continue_run():
        from orchestrator.agents.roles import ROLES
        providers={name:cls() for name,cls in PROVIDERS.items()}
        for provider in providers.values(): provider.discover()
        runner=AgentRunner(providers,router,store,workflow_id=args.workflow_id,safety=cfg.real_run,
            cost_router=CostAwareRouter(router,cfg.cost_optimization,store,args.workflow_id),
            execution_policy_router=ExecutionPolicyRouter(cfg.execution_policies,router.capabilities))
        if gate:
            runner.on_call=lambda role,provider,model,task,files,outputs,artifacts=(),attempt=1: gate.confirm("AGENT_CALL",role,provider,model,files,outputs,task_id=task,attempt=attempt,artifacts=artifacts)
            runner.on_call_complete=lambda result: gate.agent_call_completed(result)

        def command_event(event,payload):
            if event=="started":
                current=store.get_workflow(args.workflow_id)
                store.update_workflow(args.workflow_id,"RUNNING_VERIFICATION",{**current["state"],"running_command":payload["command"]},current["current_task"])
            else: store.record_verification(args.workflow_id,payload)
        harness.on_command=command_event
        def gate_callback(stage,role,files=(),commands=(),attempt=1,artifacts=(),**kwargs):
            if not gate: return True
            route=router.route(role) if role in ROLES else None
            return gate.confirm(stage,role,route.provider if route else "python",route.model if route else None,files,commands,attempt=attempt,artifacts=artifacts,extra_context=kwargs.get("extra_context"))
        def approve_constitution():
            return bool(gate and gate_callback("CONSTITUTION_CREATE","constitution",["constitution.md"],[]))
        try:
            result=run_sdd_workflow(item["feature"],root,runner,harness,cfg,root/".orchestrator"/"runs"/args.workflow_id,approve_constitution=approve_constitution,store=store,gate_callback=gate_callback,resume=True,
                                    clarification_callback=gate.ask_clarifications if gate else None)
            current=store.get_workflow(args.workflow_id)
            state={**(current["state"] if current else {}),"stage":"COMPLETE","completed_tasks":result["completed_tasks"],"traceability":result["traceability"],"verification_results":result["final_verification"]}
            store.update_workflow(args.workflow_id,"COMPLETE",state)
            (root/".orchestrator"/"reports"/f"{args.workflow_id}.json").write_text(json.dumps(state,indent=2))
        except WorkflowBlocked as exc:
            current=store.get_workflow(args.workflow_id)
            state={**(current["state"] if current else {}),"stage":"BLOCKED","reason":str(exc)}
            store.update_workflow(args.workflow_id,"BLOCKED",state,current["current_task"] if current else None)
            (root/".orchestrator"/"reports"/f"{args.workflow_id}.json").write_text(json.dumps(state,indent=2))
    try:
        report=resume_workflow(store,args.workflow_id,root,harness,interactive=gate,continue_fn=continue_run)
    except (ValueError,RuntimeError) as exc: raise SystemExit(str(exc)) from exc
    print(json.dumps(report,indent=2))


def verify(args):
    from orchestrator.verification.harness import run_verify_command
    run_verify_command(Path.cwd(), args.config)


def validate(args):
    path=Path(args.artifact)
    if not path.exists(): raise SystemExit(f"Artifact not found: {path}")
    text=path.read_text(); required=args.requirement or []
    missing=[req for req in required if req not in text]
    status="PASS" if not missing else "REVISE"
    result={"status":status,"issues":[f"Requirement ID missing: {req} in {path}" for req in missing],"summary":"Deterministic textual requirement check","validator":"python","model":None}
    print(json.dumps(result,indent=2))


def configure(args):
    """Generate reviewable configuration; never overwrite existing files."""
    root=Path.cwd(); config_path=root/"orchestrator.yaml"; models_path=root/"models.yaml"
    if config_path.exists() and models_path.exists():
        print("Configuration already exists; no files changed")
        return
    try: import yaml
    except ImportError: yaml=None
    caps=discover_all(root)
    suggestions=VerificationHarness(root).detect()
    if not config_path.exists():
        from orchestrator.agents.roles import ROLES
        roles={name:{"tier":spec.tier,"required_capabilities":list(spec.required_capabilities),"preferred_capabilities":list(spec.preferred_capabilities),"preferred_providers":list(spec.preferred_providers)} for name,spec in ROLES.items() if not getattr(spec, "deprecated", False)}
        for role,author in {"constitution_validator":"constitution","specification_validator":"specification","plan_validator":"planning","tasks_validator":"tasks","consistency_agent":"tasks","test_validator":"test_designer","coder":"test_designer","code_reviewer":"coder","final_reviewer":"coder"}.items():
            roles[role]["prefer_different_provider_from"]=[author]
        data={"providers":{"preference":["codex","opencode","agy"]},"roles":roles,"timeouts":{"provider":600,"verification":600},"retries":{"artifact_generation":3,"implementation":3,"review":2},"routing":Config().routing,"cost_optimization":Config().cost_optimization,"execution_policies":Config().execution_policies,"real_run":Config().real_run,"human_gates":{"mode":"interactive","constitution_change":True},"verification":{key:[{"name":item["name"],"command":item["command"]} for item in suggestions[key]] if key!="requirements" else [] for key in suggestions},"git":{"checkpoint_per_task":True},"logging":{"level":"INFO"}}
        config_path.write_text(("# Generated suggestions. Review commands before running workflows.\n"+yaml.safe_dump(data,sort_keys=False)) if yaml else json.dumps(data,indent=2)+"\n")
        print(f"Generated {config_path.name}")
    if not models_path.exists():
        models={}
        for provider,cap in caps.items():
            models[provider]={}
            for model_id in cap.models:
                tiers,source=describe_model(cap,provider,model_id)
                models[provider][model_id]={"tiers":[] if tiers==["unknown"] else tiers,"tier_source":source}
        models_path.write_text(("# Generated tier suggestions; set tier_source: config after manual review.\n"+yaml.safe_dump({"models":models},sort_keys=False)) if yaml else json.dumps({"models":models},indent=2)+"\n")
        print(f"Generated {models_path.name}")


def main():
    parser=argparse.ArgumentParser(prog="orchestrator",description="Spec-driven development orchestrator")
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("doctor"); p.add_argument("--config",default="orchestrator.yaml"); p.add_argument("--live",action="store_true",help="Run opt-in low-cost provider smoke calls"); p.add_argument("--verbose",action="store_true",help="Show execution, parsing, contract and failure layer for live checks"); p.set_defaults(func=doctor)
    p=sub.add_parser("models"); p.add_argument("--config",default="orchestrator.yaml"); p.set_defaults(func=models)
    p=sub.add_parser("provider-summary"); p.add_argument("--json",action="store_true"); p.set_defaults(func=provider_summary)
    p=sub.add_parser("run"); p.add_argument("--feature"); p.add_argument("--feature-file"); p.add_argument("--coder-provider"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--interactive",action="store_true"); p.add_argument("--first-real-run",action="store_true"); p.add_argument("--worktree",action="store_true"); p.add_argument("--config",default="orchestrator.yaml"); p.set_defaults(func=run)
    p=sub.add_parser("resume"); p.add_argument("workflow_id"); p.add_argument("--interactive",action="store_true"); p.add_argument("--config",default="orchestrator.yaml"); p.set_defaults(func=resume)
    p=sub.add_parser("status"); p.add_argument("workflow_id",nargs="?"); p.set_defaults(func=status)
    p=sub.add_parser("workflows"); p.set_defaults(func=workflows)
    p=sub.add_parser("inspect"); p.add_argument("workflow_id"); p.set_defaults(func=inspect)
    p=sub.add_parser("checkpoints"); p.add_argument("workflow_id"); p.set_defaults(func=checkpoints)
    p=sub.add_parser("abort"); p.add_argument("workflow_id"); p.set_defaults(func=abort)
    p=sub.add_parser("trace"); p.add_argument("requirement_id",nargs="?"); p.set_defaults(func=trace)
    p=sub.add_parser("metrics"); p.add_argument("--role"); p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--config",default="orchestrator.yaml"); p.set_defaults(func=metrics)
    p=sub.add_parser("route"); route_sub=p.add_subparsers(dest="route_command",required=True)
    q=route_sub.add_parser("explain"); q.add_argument("role"); q.add_argument("--task"); q.add_argument("--workflow-id"); q.add_argument("--escalation-reason"); q.add_argument("--json",action="store_true"); q.add_argument("--config",default="orchestrator.yaml"); q.set_defaults(func=route_explain)
    p=sub.add_parser("verify"); p.add_argument("--config",default="orchestrator.yaml"); p.set_defaults(func=verify)
    p=sub.add_parser("validate"); p.add_argument("artifact"); p.add_argument("--requirement",action="append"); p.set_defaults(func=validate)
    p=sub.add_parser("configure"); p.set_defaults(func=configure)
    args=parser.parse_args(); args.func(args)
