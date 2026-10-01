from pathlib import Path
from .models import Config


def _read_data(path: Path) -> dict:
    content=path.read_text()
    try:
        import json
        data=json.loads(content)
    except ValueError:
        try:
            import yaml
        except ImportError:
            raise RuntimeError(f"{path} uses YAML syntax; install PyYAML or use JSON syntax (valid YAML 1.2)")
        data=yaml.safe_load(content)
    return data or {}


def load_models(path: str | Path = "models.yaml") -> dict:
    p=Path(path)
    if not p.exists(): return {}
    data=_read_data(p)
    return data if isinstance(data,dict) else {}


def load_config(path: str | Path = "orchestrator.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config(path=p,models=load_models(p.parent/"models.yaml"))
    data=_read_data(p)
    external=load_models(p.parent/"models.yaml")
    inline=data.get("models",{}) if isinstance(data.get("models"),dict) else {}
    if inline and "models" not in inline: inline={"models":inline}
    model_config={**external,"models":{**external.get("models",{}),**inline.get("models",{})}}
    if "use_cli_default" in inline: model_config["use_cli_default"]=inline["use_cli_default"]
    return Config(
        providers=data.get("providers", {}), roles=data.get("roles", {}),
        retries=data.get("retries", {"artifact_generation": 3, "implementation": 3, "review": 2}),
        verification=data.get("verification", {"build": [], "tests": [], "lint": [], "static": []}),
        models=model_config,
        human_gates={**Config().human_gates, **data.get("human_gates", {})}, timeouts=data.get("timeouts", {"provider": 600, "verification": 600}), path=p,
        routing={**Config().routing,**data.get("routing",{})},
        real_run={**Config().real_run,**data.get("real_run",{})},
        cost_optimization={**Config().cost_optimization,**data.get("cost_optimization",{})},
        execution_policies={**Config().execution_policies,**data.get("execution_policies",{})},
    )
