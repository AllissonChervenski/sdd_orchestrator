import json
from argparse import Namespace

from orchestrator.config.models import AgentResult, Config, ProviderCapabilities
from orchestrator.doctor import (
    ProviderSmoke,
    SmokeCheck,
    _check_smoke,
    live_smoke_tests,
    opencode_text,
    structured_ok,
)


def _result(provider, stdout, structured=None, success=True, exit_code=0):
    return AgentResult(provider, None, "smoke", success, exit_code, stdout, "", 0, structured)


class FakeProvider:
    def __init__(self, result): self.result=result
    def smoke(self, prompt, model, cwd, timeout, structured):
        return ["fake", "--structured" if structured else "--basic", prompt],self.result


def test_agy_basic_accepts_manual_plain_stdout_and_separates_structured_check():
    raw=_result("agy","AGY_SMOKE_OK\n",success=False)
    raw.error="PROVIDER_FAILURE"
    basic=_check_smoke("agy",FakeProvider(raw),None,"/tmp",False)
    assert basic.status=="PASS" and basic.exit_code==0
    assert basic.parsing=="PASS (plain stdout)" and basic.failure_layer is None
    structured=_check_smoke("agy",FakeProvider(_result("agy",'{"response":"{\\"ok\\":true}"}',{"response":'{"ok":true}'})),None,"/tmp",True)
    assert structured.status=="PASS"


def test_opencode_jsonl_text_events_ignore_step_lifecycle_and_concatenate_parts():
    events='\n'.join((
        json.dumps({"type":"step_start","part":{"type":"step-start"}}),
        json.dumps({"type":"text","part":{"text":"OPENCODE_"}}),
        json.dumps({"type":"text","part":{"text":"SMOKE_OK"}}),
        json.dumps({"type":"step_finish","part":{"type":"step-finish"}}),
    ))
    assert opencode_text(events)=="OPENCODE_SMOKE_OK"
    result=_check_smoke("opencode",FakeProvider(_result("opencode",events)),None,"/tmp",False)
    assert result.status=="PASS" and result.parsing=="PASS (OpenCode JSONL type=text)"


def test_codex_plain_stdout_passes_without_json_and_jsonl_is_separate():
    basic=_check_smoke("codex",FakeProvider(_result("codex","CODEX_SMOKE_OK\n")),None,"/tmp",False)
    assert basic.status=="PASS"
    events='\n'.join((
        json.dumps({"type":"thread.started","prompt":'{"ok":true}'}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'{"ok":true}'}}),
        json.dumps({"type":"turn.completed"}),
    ))
    structured=_check_smoke("codex",FakeProvider(_result("codex",events)),None,"/tmp",True)
    assert structured.status=="PASS" and structured.parsing=="PASS (Codex JSONL agent_message)"
    assert not structured_ok(events)


def test_escaped_json_fragment_cannot_recurse_or_pass_from_prompt_echo():
    assert not structured_ok(r'{\"ok\":true}')
    assert not structured_ok('"CODEX_SMOKE_OK"')
    echoed=json.dumps({"type":"thread.started","prompt":'{"ok":true}'})
    failed=_check_smoke("codex",FakeProvider(_result("codex",echoed)),None,"/tmp",True)
    assert failed.status=="FAIL" and failed.failure_layer=="parsing"
    assert failed.failure_category=="PARSING_FAILURE"


def test_basic_pass_structured_fail_keeps_provider_available(monkeypatch,tmp_path):
    import orchestrator.doctor as doctor
    class Provider:
        def discover(self): return None
        def smoke(self,prompt,model,cwd,timeout,structured):
            response=('{"type":"step_start"}\n{"type":"text","part":{"text":"not JSON"}}'
                if structured else '{"type":"text","part":{"text":"OPENCODE_SMOKE_OK"}}')
            return ["opencode","run","--format","json",prompt],_result("opencode",response)
    monkeypatch.setattr(doctor,"PROVIDERS",{"opencode":Provider})
    cap=ProviderCapabilities("opencode",cli_available=True,supports_json=True)
    result=live_smoke_tests(tmp_path,{"opencode":cap})["opencode"]
    assert result.provider=="available" and result.structured_output=="degraded"
    assert result.basic_smoke.status=="PASS" and result.structured_smoke.status=="FAIL"
    assert result.structured_smoke.failure_layer=="contract"


def test_full_traceback_is_captured_for_internal_parser_error():
    class Broken:
        def smoke(self,*args): raise RecursionError("simulated parser bug")
    result=_check_smoke("codex",Broken(),None,"/tmp",False)
    assert result.failure_layer=="internal" and result.failure_category=="INTERNAL_ERROR"
    assert "RecursionError" in result.traceback and "simulated parser bug" in result.traceback


def test_doctor_verbose_shows_execution_parsing_contract_and_failure_layer(monkeypatch,tmp_path,capsys):
    import orchestrator.cli as cli
    import orchestrator.doctor as doctor
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli,"load_config",lambda path: Config())
    monkeypatch.setattr(cli,"discover_all",lambda root:{"agy":ProviderCapabilities("agy",cli_available=True)})
    monkeypatch.setattr(cli,"write_selection_report",lambda *args:None)
    check=SmokeCheck("PASS",["agy","--print","AGY_SMOKE_OK"],0,"PASS (plain stdout)","PASS (contains AGY_SMOKE_OK)")
    monkeypatch.setattr(doctor,"live_smoke_tests",lambda *args:{"agy":ProviderSmoke("available","degraded",check,SmokeCheck("FAIL",[],0,"FAIL (invalid JSON)","not attempted","parsing","PARSING_FAILURE"))})
    cli.doctor(Namespace(config="orchestrator.yaml",live=True,verbose=True))
    output=capsys.readouterr().out
    assert "provider=available structured_output=degraded" in output
    assert "execution:" in output and "exit_code: 0" in output
    assert "parsing:" in output and "contract:" in output
    assert "failure_layer: parsing" in output and "failure_category: PARSING_FAILURE" in output
