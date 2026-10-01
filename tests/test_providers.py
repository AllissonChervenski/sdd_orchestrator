from orchestrator.providers.agy import AgyProvider
from orchestrator.providers.opencode import OpenCodeProvider
from orchestrator.providers.codex import CodexProvider
from orchestrator.providers import common


def test_agy_command_uses_discovered_print_syntax():
    assert AgyProvider().build_command("hi","coder","m")==["agy","--output-format","json","--model","m","--print","hi"]
    assert AgyProvider().build_command("hi","test_validator","m",permissions="read")[-4:]==["--mode","plan","--print","hi"]
    basic=AgyProvider().build_smoke_command("AGY_SMOKE_OK","m")
    structured=AgyProvider().build_smoke_command('{"ok":true}',"m",structured=True)
    assert basic[-2:]==["--print","AGY_SMOKE_OK"] and "--output-format" not in basic
    assert structured[-2:]==["--print",'{"ok":true}'] and "--output-format" in structured
    with_perms = AgyProvider()
    with_perms._skip_permissions = True
    assert "--dangerously-skip-permissions" in with_perms.build_command("hi","coder","m")
    assert "--dangerously-skip-permissions" not in with_perms.build_command("hi","test_validator","m",permissions="read")


def test_opencode_command():
    provider=OpenCodeProvider(); provider._run_help="--model --format --agent"
    assert provider.build_command("hi","coder","m")==["opencode","run","--model","m","--format","json","hi"]
    provider.agent_map={"coder":"build"}
    assert "--agent" in provider.build_command("hi","coder","m")
    provider._run_help += " --dir"
    isolated=provider.build_command("hi","coder","m",cwd="/tmp/isolated")
    assert isolated[isolated.index("--dir"):isolated.index("--dir")+2]==["--dir","/tmp/isolated"]
    assert isolated[-1]=="hi"
    smoke=provider.build_smoke_command("OPENCODE_SMOKE_OK","m","/tmp/isolated")
    assert smoke[:4]==["opencode","run","--format","json"] and "--dir" in smoke


def test_codex_validator_gets_read_only_sandbox():
    provider=CodexProvider(); provider._exec_help="--sandbox --model --json"
    cmd=provider.build_command("hi","test_validator","m")
    assert "read-only" in cmd and "--model" in cmd
    provider._exec_help += " --cd"
    assert provider.build_command("hi","test_validator","m",cwd="/tmp/isolated")[-3:]==["--cd","/tmp/isolated","hi"]
    basic=provider.build_smoke_command("CODEX_SMOKE_OK","m","/tmp/isolated")
    structured=provider.build_smoke_command('{"ok":true}',"m","/tmp/isolated",structured=True)
    assert "read-only" in basic and "--json" not in basic
    assert "--json" in structured and "--cd" in structured


def test_codex_coder_gets_workspace_sandbox():
    provider=CodexProvider(); provider._exec_help="--sandbox"
    assert "workspace-write" in provider.build_command("hi","coder")


def test_mise_binary_resolution_uses_installed_executable(tmp_path, monkeypatch):
    binary=tmp_path/"codex"; binary.write_text("#!/bin/sh\nexit 0\n"); binary.chmod(0o755)
    monkeypatch.setattr(common.shutil,"which",lambda name: "/usr/bin/mise" if name=="mise" else "/home/user/.local/bin/codex")
    class Result:
        returncode=0
        stdout=str(binary)+"\n"
    monkeypatch.setattr(common.subprocess,"run",lambda *a,**kw: Result())
    assert common.resolve_binary("codex")==str(binary)
