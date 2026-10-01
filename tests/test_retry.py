from orchestrator.agents.runner import AgentRunner
from orchestrator.agents.router import ModelRouter
from orchestrator.config.models import AgentResult


class Fake:
    def __init__(self,name,success): self.name=name; self.success=success; self.calls=0
    def run(self,*args): self.calls+=1; return AgentResult(self.name,None,args[1],self.success,0 if self.success else 1)


def test_provider_failure_falls_back():
    caps={x:type("C",(),{"cli_available":True,"models":[],"supports_file_editing":True,"supports_shell":True})() for x in ("opencode","codex")}
    providers={"opencode":Fake("opencode",False),"codex":Fake("codex",True)}
    runner=AgentRunner(providers,ModelRouter(caps))
    assert runner.run("coder","prompt").success
    assert providers["opencode"].calls==providers["codex"].calls==1


def test_validation_uses_self_only_when_no_independent_provider():
    caps={"agy":type("C",(),{"cli_available":True,"models":[]})()}
    providers={"agy":Fake("agy",True)}
    runner=AgentRunner(providers,ModelRouter(caps))
    result=runner.run("specification_validator","review",exclude_providers={"agy"})
    assert result.success
    assert result.usage["validation_independence"] is False
