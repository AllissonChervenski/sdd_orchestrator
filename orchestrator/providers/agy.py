import re
import json
from orchestrator.config.models import ProviderCapabilities, ModelCapabilities
from .base import AgentProvider
from .common import execute, probe


def parse_models(output: str) -> list[str]:
    """Parse `agy models` table rows: model id, then display name separated by tabs."""
    try:
        payload=json.loads(output)
        entries=payload.get("command",{}).get("data",{}).get("models",[])
        if entries: return [entry["id"] for entry in entries if isinstance(entry,dict) and isinstance(entry.get("id"),str)]
        output=payload.get("response",output)
    except (ValueError,TypeError,AttributeError): pass
    models=[]
    for line in output.splitlines():
        fields=re.split(r"\s+",line.strip())
        if fields and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*",fields[0]) and fields[0] not in {"Fetching","Available","Models"}:
            models.append(fields[0])
    return list(dict.fromkeys(models))


def parse_model_details(output: str) -> list[ModelCapabilities]:
    try:
        payload=json.loads(output)
        entries=payload.get("command",{}).get("data",{}).get("models",[])
        if entries: return [ModelCapabilities("agy",entry["id"],entry.get("label"),metadata_source="agy --output-format json models",confidence="high") for entry in entries if isinstance(entry,dict) and isinstance(entry.get("id"),str)]
        output=payload.get("response",output)
    except (ValueError,TypeError,AttributeError): pass
    details=[]
    for line in output.splitlines():
        fields=re.split(r"\s{2,}|\t+",line.strip(),maxsplit=1)
        if fields and fields[0] in parse_models(line):
            details.append(ModelCapabilities("agy",fields[0],fields[1].strip() if len(fields)>1 else None,
                                             metadata_source="agy models",confidence="high"))
    return details


class AgyProvider(AgentProvider):
    name = "agy"
    def __init__(self):
        self._models: list[str] = []
        self._skip_permissions: bool = False
        self._disable_slash_commands: bool = False
    def discover(self):
        ok, version, err = probe("agy", ["--version"])
        help_ok, help_text, help_err = probe("agy", ["--help"])
        self._skip_permissions = "--dangerously-skip-permissions" in help_text
        self._disable_slash_commands = "--disable-slash-commands" in help_text
        model_ok, model_text, model_err = probe("agy", ["--output-format", "json", "models"],timeout=90)
        if not model_ok or not parse_models(model_text):
            model_ok, model_text, model_err = probe("agy", ["models"],timeout=90)
        self._models = parse_models(model_text) if model_ok else []
        return ProviderCapabilities(self.name, ok, version if ok else None, self._models, "--print" in help_text or "-p" in help_text, "json" in help_text, "--continue" in help_text or "--conversation" in help_text, "--model" in help_text, "--agent" in help_text, True, "terminal" in help_text, metadata={"help": help_text, "errors": [x for x in (err, help_err, model_err) if x], "model_discovery_error": model_err if not model_ok else None}, model_details=parse_model_details(model_text) if model_ok else [])
    def build_command(self, prompt, role, model=None, cwd=None, permissions=None):
        cmd = ["agy"]
        if getattr(self, "_disable_slash_commands", False):
            cmd += ["--disable-slash-commands"]
        if getattr(self, "_skip_permissions", False) and permissions != "read":
            cmd += ["--dangerously-skip-permissions"]
        cmd += ["--output-format", "json"]
        if model: cmd += ["--model", model]
        if permissions == "read": cmd += ["--mode", "plan"]
        # --print consumes the following argument as its prompt.
        cmd += ["--print", prompt]
        return cmd
    def run(self, prompt, role, model=None, cwd=None, timeout=None, permissions=None):
        return execute(self.name, model, role, self.build_command(prompt, role, model, cwd, permissions), cwd, timeout)
    def run_skill(self, skill, arguments, role, model=None, cwd=None, timeout=None, permissions=None, policy_prefix=""):
        if skill.delivery != "native" or getattr(self, "_disable_slash_commands", False):
            return super().run_skill(skill, arguments, role, model, cwd, timeout, permissions, policy_prefix)
        prompt = f"/skill:{skill.name} {arguments}".rstrip()
        if policy_prefix: prompt = f"{prompt}\n\n{policy_prefix}"
        return self.run(prompt, role, model, cwd, timeout, permissions)
    def build_smoke_command(self, prompt, model=None, cwd=None, structured=False):
        cmd = ["agy"]
        if getattr(self, "_disable_slash_commands", False):
            cmd += ["--disable-slash-commands"]
        if structured: cmd += ["--output-format","json"]
        if model: cmd += ["--model",model]
        cmd += ["--mode","plan","--print",prompt]
        return cmd
    def smoke(self, prompt, model=None, cwd=None, timeout=45, structured=False):
        command=self.build_smoke_command(prompt,model,cwd,structured)
        return command,execute(self.name,model,"smoke",command,cwd,timeout)
    def list_models(self): return list(self._models)
