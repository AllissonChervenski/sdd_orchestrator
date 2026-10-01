import json
import subprocess
import tempfile
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from orchestrator.providers import PROVIDERS


def discover_all(root: str | Path = "."):
    root = Path(root)
    cache_path=root / ".orchestrator" / "capabilities.json"
    try: previous=json.loads(cache_path.read_text())
    except (OSError,ValueError): previous={}
    out = {}
    from orchestrator.agents.router import inferred_capabilities
    for name, cls in PROVIDERS.items():
        cap = cls().discover()
        old=previous.get(name,{})
        if not cap.models and old.get("models") and old.get("cli_version")==cap.cli_version:
            cap.models=list(old["models"])
            from orchestrator.config.models import ModelCapabilities
            cap.model_details=[ModelCapabilities(**item) for item in old.get("model_details",[]) if isinstance(item,dict)]
            cap.metadata["model_discovery"]="reused last successful catalog because this refresh returned no models"
        cap.capabilities=sorted(inferred_capabilities(name,cap))
        cap.metadata["capability_source"]="provider behavior and observed CLI flags"
        out[name] = cap
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({k: asdict(v) for k, v in out.items()}, indent=2))
    (cache_path.parent/"models.json").write_text(json.dumps({name:[asdict(detail) for detail in cap.model_details] for name,cap in out.items()},indent=2))
    return out


def write_selection_report(root, capabilities, router):
    lines = ["# Model selection report", "", "Routing uses configured tiers, CLI metadata, history, then low-confidence heuristics. No model is claimed superior without measured evidence.", "", "| Role | Provider | Model | Tier | Tier source | Independence | Reason |", "|---|---|---|---|---|---|---|"]
    from orchestrator.agents.roles import ROLES
    from orchestrator.agents.plan import build_route_plan
    canonical_roles = {name: role for name, role in ROLES.items() if not getattr(role, "deprecated", False)}
    plan=build_route_plan(router,list(canonical_roles))
    for name, role in canonical_roles.items():
        route=plan[name]
        independence="n/a" if route.independence is None else str(route.independence).lower()
        lines.append(f"| `{name}` | `{route.provider}` | `{route.model or 'CLI default'}` | `{route.tier}` | `{route.tier_source}` | `{independence}` | {route.reason} |")
    path = Path(root) / ".orchestrator" / "model-selection.md"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("\n".join(lines)+"\n")


@dataclass
class SmokeCheck:
    status: str
    execution: list[str]
    exit_code: int | None
    parsing: str
    contract: str
    failure_layer: str | None = None
    failure_category: str | None = None
    error: str | None = None
    traceback: str | None = None


@dataclass
class ProviderSmoke:
    provider: str
    structured_output: str
    basic_smoke: SmokeCheck
    structured_smoke: SmokeCheck


def _jsonl_events(output):
    events=[]
    for number,line in enumerate(output.splitlines(),1):
        if not line.strip(): continue
        try: event=json.loads(line)
        except ValueError as exc: raise ValueError(f"invalid JSONL at line {number}: {exc}") from exc
        if not isinstance(event,dict): raise ValueError(f"JSONL line {number} is not an object")
        events.append(event)
    if not events: raise ValueError("JSONL stream is empty")
    return events


def opencode_text(output):
    """Read model text events; step_start and step_finish are lifecycle events."""
    parts=[]
    for event in _jsonl_events(output):
        if event.get("type")!="text": continue
        part=event.get("part")
        if isinstance(part,dict) and isinstance(part.get("text"),str): parts.append(part["text"])
    if not parts: raise ValueError("OpenCode JSONL contains no type=text part.text event")
    return "".join(parts)


def codex_text(output):
    """Read the final assistant message from Codex JSONL, never prompt metadata."""
    messages=[]
    for event in _jsonl_events(output):
        if event.get("type")!="item.completed": continue
        item=event.get("item")
        if isinstance(item,dict) and item.get("type")=="agent_message" and isinstance(item.get("text"),str):
            messages.append(item["text"])
    if not messages: raise ValueError("Codex JSONL contains no completed agent_message")
    return messages[-1]


def structured_ok(value):
    """The final answer must itself be a strict JSON object with ok=true."""
    try: payload=json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError): return False
    return isinstance(payload,dict) and payload.get("ok") is True


def _answer(name, outcome, structured):
    if name=="agy" and structured:
        payload=outcome.structured_output
        if not isinstance(payload,dict):
            try: payload=json.loads(outcome.stdout)
            except ValueError as exc: raise ValueError("AGY output is not a JSON object") from exc
        if not isinstance(payload,dict) or "response" not in payload:
            raise ValueError("AGY JSON has no response field")
        return payload["response"],"AGY response JSON"
    if name=="opencode": return opencode_text(outcome.stdout),"OpenCode JSONL type=text"
    if name=="codex" and structured: return codex_text(outcome.stdout),"Codex JSONL agent_message"
    if not outcome.stdout.strip(): raise ValueError("plain stdout is empty")
    return outcome.stdout,"plain stdout"


def _check_smoke(name, provider, model, directory, structured):
    token={"agy":"AGY_SMOKE_OK","opencode":"OPENCODE_SMOKE_OK","codex":"CODEX_SMOKE_OK"}[name]
    prompt=('Return only the JSON object {"ok":true}. Do not edit files.' if structured
            else f"Responda somente com: {token}. Não edite arquivos.")
    command=[]
    try:
        command,outcome=provider.smoke(prompt,model,directory,45,structured)
        # The subprocess exit code is authoritative for execution. A stale
        # adapter success/error flag must not turn exit 0 into provider failure.
        if outcome.exit_code!=0:
            return SmokeCheck("FAIL",command,outcome.exit_code,"not attempted","not attempted", "execution","PROVIDER_FAILURE",outcome.error or outcome.stderr.strip() or f"exit {outcome.exit_code}")
        try: answer,format_name=_answer(name,outcome,structured)
        except (ValueError,TypeError) as exc:
            return SmokeCheck("FAIL",command,outcome.exit_code,f"FAIL ({exc})","not attempted","parsing","PARSING_FAILURE",str(exc))
        passed=structured_ok(answer) if structured else token in answer
        contract='JSON object with ok=true' if structured else f'contains {token}'
        return SmokeCheck("PASS" if passed else "FAIL",command,outcome.exit_code,f"PASS ({format_name})",f"{'PASS' if passed else 'FAIL'} ({contract})",
                          None if passed else "contract",None if passed else "CONTRACT_MISMATCH")
    except Exception as exc:
        return SmokeCheck("FAIL",command,None,"not attempted","not attempted","internal","INTERNAL_ERROR",f"{type(exc).__name__}: {exc}",traceback.format_exc())


def live_smoke_tests(root, capabilities, models_config=None):
    """Two explicit, low-cost checks per provider in disposable Git workspaces."""
    from orchestrator.agents.router import select_model
    results={}
    skipped=SmokeCheck("SKIP",[],None,"not attempted","not attempted","availability","CLI_UNAVAILABLE")
    for name,cap in capabilities.items():
        if not cap.cli_available:
            results[name]=ProviderSmoke("unavailable","unavailable",skipped,skipped)
            continue
        with tempfile.TemporaryDirectory(prefix=f"orchestrator-{name}-") as directory:
            subprocess.run(["git","init","-q",directory],capture_output=True,check=False)
            provider=PROVIDERS[name]()
            try: provider.discover()
            except Exception as exc:
                failed=SmokeCheck("FAIL",[],None,"not attempted","not attempted","discovery","DISCOVERY_FAILURE",f"{type(exc).__name__}: {exc}",traceback.format_exc())
                results[name]=ProviderSmoke("unavailable","unavailable",failed,failed)
                continue
            model,_,_,_=select_model(cap,name,"fast",models_config or {})
            basic=_check_smoke(name,provider,model,directory,False)
            structured=_check_smoke(name,provider,model,directory,True) if cap.supports_json else SmokeCheck("SKIP",[],None,"not attempted","not attempted","capability","JSON_UNAVAILABLE")
            available=basic.status=="PASS"
            results[name]=ProviderSmoke("available" if available else "unavailable",
                "supported" if structured.status=="PASS" else ("degraded" if available else "unavailable"),basic,structured)
    return results
