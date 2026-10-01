import argparse
import subprocess

import pytest

from orchestrator.agents.router import ModelRouter
from orchestrator.agents.runner import AgentRunner
from orchestrator.cli import run
from orchestrator.config.models import AgentResult, ProviderCapabilities


class Provider:
    def __init__(self,changed=None,success=True): self.changed=changed; self.calls=0; self.success=success
    def run(self,prompt,role,model,cwd,timeout,permissions):
        self.calls+=1
        if self.changed:
            path=cwd/self.changed; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("modified")
        return AgentResult("codex",model,role,self.success,exit_code=0 if self.success else 1,error=None if self.success else "PROVIDER_FAILURE")


def _runner(provider,safety):
    cap=ProviderCapabilities("codex",cli_available=True,models=["m"],supports_model_selection=True,supports_file_editing=True,supports_shell=True)
    router=ModelRouter({"codex":cap},{"routing":{"adaptive_routing_mode":"observe"},"models_config":{"models":{"codex":{"m":{"tiers":["coding_strong"]}}}}})
    return AgentRunner({"codex":provider},router,safety=safety)


def test_strict_file_scope_blocks_out_of_scope_write(tmp_path):
    provider=Provider("outside.py")
    runner=_runner(provider,{"safety_mode":"strict"})
    result=runner.run("coder","implement",cwd=tmp_path,task={"id":"T1"},allowed_paths=["src/"])
    assert not result.success and result.error.startswith("SCOPE_VIOLATION")
    assert provider.calls==1
    assert (tmp_path/"outside.py").exists() # preserve evidence; never reset user files


def test_strict_requires_task_scope_and_enforces_budget(tmp_path):
    provider=Provider("src/feature.py")
    runner=_runner(provider,{"safety_mode":"strict","max_agent_calls_per_task":1,"max_agent_calls_per_workflow":2,"max_wall_time":60})
    missing=runner.run("coder","implement",cwd=tmp_path,task={"id":"T1"})
    assert missing.error.startswith("SCOPE_VIOLATION") and provider.calls==0
    first=runner.run("coder","implement",cwd=tmp_path,task={"id":"T1"},allowed_paths=["src/"])
    assert first.success
    second=runner.run("coder","implement",cwd=tmp_path,task={"id":"T1"},allowed_paths=["src/"])
    assert second.error.startswith("BUDGET_EXCEEDED") and provider.calls==1


def test_validator_is_read_only_even_if_provider_ignores_permissions(tmp_path):
    provider=Provider("tests/test_x.py")
    runner=_runner(provider,{"safety_mode":"strict"})
    outcome=runner.run("test_validator","review",cwd=tmp_path,task={"id":"T1"})
    assert outcome.error.startswith("SCOPE_VIOLATION")


def test_partial_write_blocks_provider_fallback(tmp_path):
    provider=Provider("src/feature.py",success=False)
    runner=_runner(provider,{"safety_mode":"strict"})
    result=runner.run("coder","implement",cwd=tmp_path,task={"id":"T1"},allowed_paths=["src/"])
    assert result.error.startswith("PARTIAL_WRITE")
    assert provider.calls==1


def test_first_real_run_blocks_dirty_workspace_before_agent_call(tmp_path,monkeypatch):
    subprocess.run(["git","init","-q",str(tmp_path)],check=True)
    (tmp_path/"file.txt").write_text("dirty")
    monkeypatch.chdir(tmp_path)
    args=argparse.Namespace(config="orchestrator.yaml",feature="Feature",feature_file=None,
        coder_provider=None,dry_run=False,interactive=False,first_real_run=True,worktree=False)
    with pytest.raises(SystemExit,match="uncommitted changes"):
        run(args)
    assert not (tmp_path/".orchestrator"/"runs").exists()


def test_strict_real_run_requires_git_repo(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    args=argparse.Namespace(config="orchestrator.yaml",feature="Feature",feature_file=None,
        coder_provider=None,dry_run=False,interactive=False,first_real_run=True,worktree=False)
    with pytest.raises(SystemExit,match="requires a Git repository"):
        run(args)
