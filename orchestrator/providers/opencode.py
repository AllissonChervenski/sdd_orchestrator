import re
import json
from orchestrator.config.models import ProviderCapabilities, ModelCapabilities
from .base import AgentProvider
from .common import execute, probe


class OpenCodeProvider(AgentProvider):
    name = "opencode"
    def __init__(self, agent_map=None): self._models = []; self._run_help = ""; self.agent_map=agent_map or {}
    def discover(self):
        ok, version, err = probe("opencode", ["--version"])
        help_ok, help_text, help_err = probe("opencode", ["--help"])
        models_ok, model_text, model_err = probe("opencode", ["models", "--refresh"],timeout=90)
        verbose_ok, verbose_text, verbose_err = probe("opencode", ["models", "--verbose"],timeout=90) if models_ok else (False,"","")
        run_ok, run_text, run_err = probe("opencode", ["run", "--help"])
        self._run_help = run_text if run_ok else ""
        if models_ok: self._models = list(dict.fromkeys(re.findall(r"(?m)^\s*([\w.-]+/[\w.-]+)\s*$", model_text)))
        return ProviderCapabilities(self.name, ok, version.splitlines()[0] if ok else None, self._models, "run" in help_text, "--format" in run_text, "session" in (help_text+run_text).lower(), "--model" in run_text, "--agent" in run_text, True, True, metadata={"help": help_text, "run_help": run_text, "errors": [x for x in (err, help_err, model_err, verbose_err, run_err) if x], "model_discovery_error": model_err if not models_ok else None}, model_details=parse_model_details(verbose_text if verbose_ok else model_text, self._models))
    def build_command(self, prompt, role, model=None, cwd=None, permissions=None):
        cmd = ["opencode", "run"]
        if model and "--model" in self._run_help: cmd += ["--model", model]
        if "--format" in self._run_help: cmd += ["--format", "json"]
        if cwd and "--dir" in self._run_help: cmd += ["--dir", str(cwd)]
        # Orchestrator roles are prompt roles, not necessarily installed OpenCode agents.
        if role in self.agent_map and "--agent" in self._run_help: cmd += ["--agent", self.agent_map[role]]
        cmd.append(prompt)
        return cmd
    def run(self, prompt, role, model=None, cwd=None, timeout=None, permissions=None): return execute(self.name, model, role, self.build_command(prompt, role, model, cwd, permissions), cwd, timeout)
    def run_skill(self, skill, arguments, role, model=None, cwd=None, timeout=None, permissions=None, policy_prefix=""):
        if skill.delivery != "native":
            return super().run_skill(skill, arguments, role, model, cwd, timeout, permissions, policy_prefix)
        cmd=self.build_command(arguments,role,model,cwd,permissions)
        cmd[2:2]=["--command",skill.command]
        if policy_prefix: cmd[-1]=f"{arguments}\n\n{policy_prefix}"
        return execute(self.name,model,role,cmd,cwd,timeout)
    def build_smoke_command(self, prompt, model=None, cwd=None, structured=False):
        cmd=["opencode","run","--format","json"]
        if model and "--model" in self._run_help: cmd += ["--model",model]
        if cwd and "--dir" in self._run_help: cmd += ["--dir",str(cwd)]
        cmd.append(prompt)
        return cmd
    def smoke(self, prompt, model=None, cwd=None, timeout=45, structured=False):
        command=self.build_smoke_command(prompt,model,cwd,structured)
        return command,execute(self.name,model,"smoke",command,cwd,timeout)
    def list_models(self): return list(self._models)


def parse_model_details(output: str, model_ids: list[str]) -> list[ModelCapabilities]:
    """Parse the installed CLI's `model-id` + JSON verbose blocks."""
    decoder=json.JSONDecoder()
    by_id={}
    for match in re.finditer(r"(?m)^([\w.-]+/[\w.-]+)\s*$",output):
        model_id=match.group(1)
        if model_id not in model_ids: continue
        rest=output[match.end():].lstrip()
        try: data,_=decoder.raw_decode(rest)
        except ValueError: continue
        if not isinstance(data,dict): continue
        limit=data.get("limit",{}) or {}; caps=data.get("capabilities",{}) or {}; costs=data.get("cost",{}) or {}
        context=limit.get("context")
        by_id[model_id]=ModelCapabilities("opencode",model_id,data.get("name"),available=data.get("status","active")=="active",
            context_window=context if isinstance(context,int) else None,
            supports_reasoning=caps.get("reasoning") if isinstance(caps.get("reasoning"),bool) else None,
            metadata_source="opencode models --verbose",confidence="high",
            metadata={"family":data.get("family"),"toolcall":caps.get("toolcall"),"input_cost":costs.get("input"),"output_cost":costs.get("output")})
    return [by_id.get(model_id,ModelCapabilities("opencode",model_id,metadata_source="opencode models --refresh",confidence="medium")) for model_id in model_ids]
