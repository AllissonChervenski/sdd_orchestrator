from orchestrator.config.models import ProviderCapabilities, ModelCapabilities
from .base import AgentProvider
from .common import execute, probe
import json


def parse_models(output: str) -> list[str]:
    """Read the raw catalog from the installed CLI's `codex debug models` command."""
    try:
        data=json.loads(output)
    except (ValueError, TypeError):
        return []
    models=data.get("models", []) if isinstance(data,dict) else []
    return list(dict.fromkeys(item["slug"] for item in models if isinstance(item,dict) and isinstance(item.get("slug"),str) and item.get("visibility","list")=="list" and item.get("supported_in_api",True)))


def parse_model_details(output: str) -> list[ModelCapabilities]:
    try: catalog=json.loads(output)
    except (ValueError,TypeError): return []
    entries={item.get("slug"):item for item in catalog.get("models",[]) if isinstance(item,dict)}
    details=[]
    for model_id in parse_models(output):
        item=entries[model_id]
        context=item.get("context_window")
        details.append(ModelCapabilities("codex",model_id,item.get("display_name"),context_window=context if isinstance(context,int) else None,
            supports_reasoning=bool(item.get("supported_reasoning_levels")) if "supported_reasoning_levels" in item else None,
            metadata_source="codex debug models",confidence="high",metadata={k:item[k] for k in ("description","priority","default_reasoning_level","supported_reasoning_levels") if k in item}))
    return details


class CodexProvider(AgentProvider):
    name = "codex"
    def __init__(self): self._models = []; self._exec_help = ""
    def discover(self):
        ok, version, err = probe("codex", ["--version"])
        help_ok, help_text, help_err = probe("codex", ["--help"])
        exec_ok, exec_text, exec_err = probe("codex", ["exec", "--help"])
        self._exec_help = exec_text if exec_ok else ""
        catalog_ok, catalog_text, catalog_err = probe("codex", ["debug", "models"])
        self._models = parse_models(catalog_text) if catalog_ok else []
        return ProviderCapabilities(self.name, ok, version.splitlines()[0] if ok else None, self._models, exec_ok, "json" in exec_text, "resume" in help_text, "--model" in exec_text, False, True, True, metadata={"help": help_text, "exec_help": exec_text, "errors": [x for x in (err, help_err, exec_err, catalog_err) if x], "model_catalog_available":catalog_ok, "model_discovery_error":catalog_err if not catalog_ok else None}, model_details=parse_model_details(catalog_text) if catalog_ok else [])
    def build_command(self, prompt, role, model=None, cwd=None, permissions=None):
        read_only = permissions == "read" or role.lower().endswith("validator") or role in ("reviewer", "code_reviewer", "final_reviewer", "test_validator")
        cmd = ["codex", "exec"]
        if "--sandbox" in self._exec_help:
            cmd += ["--sandbox", "read-only" if read_only else "workspace-write"]
        if model and "--model" in self._exec_help: cmd += ["--model", model]
        if "--json" in self._exec_help: cmd += ["--json"]
        if cwd and "--cd" in self._exec_help: cmd += ["--cd", str(cwd)]
        cmd.append(prompt)
        return cmd
    def run(self, prompt, role, model=None, cwd=None, timeout=None, permissions=None): return execute(self.name, model, role, self.build_command(prompt, role, model, cwd, permissions), cwd, timeout)
    def run_skill(self, skill, arguments, role, model=None, cwd=None, timeout=None, permissions=None, policy_prefix=""):
        if skill.delivery != "native":
            return super().run_skill(skill, arguments, role, model, cwd, timeout, permissions, policy_prefix)
        prompt=f"${skill.name} {arguments}".rstrip()
        if policy_prefix: prompt=f"{prompt}\n\n{policy_prefix}"
        return self.run(prompt, role, model, cwd, timeout, permissions)
    def build_smoke_command(self, prompt, model=None, cwd=None, structured=False):
        cmd=["codex","exec"]
        if "--sandbox" in self._exec_help: cmd += ["--sandbox","read-only"]
        if model and "--model" in self._exec_help: cmd += ["--model",model]
        if cwd and "--cd" in self._exec_help: cmd += ["--cd",str(cwd)]
        if structured and "--json" in self._exec_help: cmd += ["--json"]
        cmd.append(prompt)
        return cmd
    def smoke(self, prompt, model=None, cwd=None, timeout=45, structured=False):
        command=self.build_smoke_command(prompt,model,cwd,structured)
        return command,execute(self.name,model,"smoke",command,cwd,timeout)
    def list_models(self): return list(self._models)
