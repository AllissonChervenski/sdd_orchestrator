import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def extract_usage(provider: str, stdout: str, structured: Any = None) -> dict[str, int | float]:
    """Keep only numeric counters explicitly reported by the CLI."""
    events=[]
    if isinstance(structured,dict): events.append(structured)
    if provider in {"codex","opencode"}:
        for line in stdout.splitlines():
            try: item=json.loads(line)
            except ValueError: continue
            if isinstance(item,dict): events.append(item)
    usage={}
    for event in events:
        data=event.get("usage")
        if provider=="opencode" and event.get("type")=="step_finish":
            part=event.get("part") or {}
            data=part.get("tokens") if isinstance(part,dict) else None
            reported_cost=part.get("cost") if isinstance(part,dict) else None
            if isinstance(reported_cost,(int,float)) and not isinstance(reported_cost,bool): usage["reported_cost"]=reported_cost
        if not isinstance(data,dict): continue
        for key,aliases in {"input_tokens":("input_tokens","input"),"output_tokens":("output_tokens","output"),"total_tokens":("total_tokens","total")}.items():
            value=next((data[name] for name in aliases if isinstance(data.get(name),(int,float)) and not isinstance(data.get(name),bool)),None)
            if value is not None: usage[key]=int(value)
    if "total_tokens" not in usage and "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"]=usage["input_tokens"]+usage["output_tokens"]
    return usage


def extract_model_resolution(provider: str, stdout: str, structured: Any = None) -> tuple[str | None, str, str | None]:
    """Extract (resolved_model, resolution_source, resolved_effort) from provider output.
    
    resolution_source is one of:
      - 'provider_reported': the CLI/API explicitly reported the model in stdout/structured output
      - 'config_inferred': inferred deterministically from configuration
      - 'unavailable': the provider CLI does not report the model in its output stream
    """
    resolved_model = None
    resolved_effort = None

    if provider == "opencode":
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                part = event.get("part")
                if isinstance(part, dict) and part.get("model"):
                    resolved_model = str(part["model"])
                elif event.get("model"):
                    resolved_model = str(event["model"])
                if event.get("effort") or event.get("reasoning_effort"):
                    resolved_effort = str(event.get("effort") or event.get("reasoning_effort"))
        if resolved_model:
            return resolved_model, "provider_reported", resolved_effort

    elif provider == "codex":
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                if event.get("type") in ("model", "turn_start") and event.get("model"):
                    resolved_model = str(event["model"])
                elif event.get("model"):
                    resolved_model = str(event["model"])
                if event.get("effort") or event.get("reasoning_effort"):
                    resolved_effort = str(event.get("effort") or event.get("reasoning_effort"))
        if resolved_model:
            return resolved_model, "provider_reported", resolved_effort

    elif provider == "agy":
        if isinstance(structured, dict):
            if structured.get("model"):
                resolved_model = str(structured["model"])
            elif structured.get("model_id"):
                resolved_model = str(structured["model_id"])
            if structured.get("effort"):
                resolved_effort = str(structured["effort"])
        if resolved_model:
            return resolved_model, "provider_reported", resolved_effort

    return None, "unavailable", None


def resolve_binary(binary: str) -> str | None:
    """Resolve mise-managed shims to their real installed executable when possible."""
    mise = shutil.which("mise")
    if mise:
        try:
            cp = subprocess.run([mise, "which", binary], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
            resolved = (cp.stdout or "").strip().splitlines()
            if cp.returncode == 0 and resolved:
                candidate = Path(resolved[-1])
                if candidate.is_file() and candidate.stat().st_mode & 0o111:
                    return str(candidate)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return shutil.which(binary)


def execute(provider: str, model: str | None, role: str, command: list[str], cwd: Any, timeout: int | None) -> Any:
    from orchestrator.config.models import AgentResult
    start = time.monotonic()
    requested_effort = None
    if command and "--effort" in command:
        try:
            idx = command.index("--effort")
            if idx + 1 < len(command):
                requested_effort = command[idx + 1]
        except (ValueError, IndexError):
            pass
    try:
        resolved = resolve_binary(command[0]) if command else None
        if not resolved:
            raise FileNotFoundError(f"CLI executable not found: {command[0] if command else '(empty command)'}")
        cp = subprocess.run([resolved, *command[1:]], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)
        out = cp.stdout or ""
        structured = None
        try:
            structured = json.loads(out)
        except (ValueError, TypeError):
            pass
        resolved_model, resolution_source, resolved_effort = extract_model_resolution(provider, out, structured)
        return AgentResult(
            provider, model, role, cp.returncode == 0, cp.returncode, out, cp.stderr or "", time.monotonic()-start, structured,
            usage=extract_usage(provider, out, structured), error=None if cp.returncode == 0 else "PROVIDER_FAILURE",
            resolved_model=resolved_model, resolution_source=resolution_source,
            requested_effort=requested_effort, resolved_effort=resolved_effort
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AgentResult(
            provider, model, role, False, None, duration=time.monotonic()-start, error=f"PROVIDER_FAILURE: {exc}",
            resolved_model=None, resolution_source="unavailable", requested_effort=requested_effort
        )



def probe(binary: str, args: list[str], timeout: int = 20) -> tuple[bool, str, str]:
    path = resolve_binary(binary)
    if not path:
        return False, "", f"{binary} unavailable"
    try:
        cp = subprocess.run([path, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)
        output=(cp.stdout or "")
        if cp.returncode == 0 and not output.strip(): output=cp.stderr or ""
        error=((cp.stderr or "")+(cp.stdout or "")) if cp.returncode else ""
        return cp.returncode == 0, output.strip(), error.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "", str(exc)
