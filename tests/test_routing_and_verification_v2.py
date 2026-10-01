import json
from types import SimpleNamespace

from orchestrator.agents.router import ModelRouter, select_model
from orchestrator.agents.plan import format_route
from orchestrator.config.models import ModelCapabilities
from orchestrator.providers.agy import parse_models as parse_agy, parse_model_details as agy_details
from orchestrator.providers.opencode import parse_model_details as opencode_details
from orchestrator.verification.harness import VerificationHarness, final_verification_pass
from orchestrator.tdd import TDDGate
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase


def cap(provider, models, details=(), available=True):
    return SimpleNamespace(provider=provider,cli_available=available,models=models,model_details=list(details),
        supports_model_selection=True,supports_file_editing=True,supports_shell=True,supports_json=True,
        capabilities=["CODING","AGENTIC_CODING","REASONING","VALIDATION","DOCUMENT_GENERATION","FILE_EDITING","SHELL"],metadata={})


def test_agy_json_catalog_is_explicit():
    raw=json.dumps({"command":{"data":{"models":[{"id":"gemini-x","label":"Gemini X"}]}}})
    assert parse_agy(raw)==["gemini-x"]
    assert agy_details(raw)[0].display_name=="Gemini X"


def test_opencode_verbose_metadata_is_parsed_without_inventing_capabilities():
    raw='provider/model\n{"name":"Model","status":"active","limit":{"context":12345},"capabilities":{"reasoning":true,"toolcall":true},"cost":{"input":0.1}}\n'
    detail=opencode_details(raw,["provider/model"])[0]
    assert detail.context_window==12345 and detail.supports_reasoning is True
    assert detail.supports_coding is None and detail.metadata["input_cost"]==0.1


def test_tier_source_priority_and_explicit_model():
    details=[ModelCapabilities("codex","model-meta",metadata_source="cli",metadata={"description":"Fast affordable model"}),
             ModelCapabilities("codex","model-config",display_name="Ordinary model",metadata_source="cli")]
    capability=cap("codex",["model-meta","model-config"],details)
    config={"models":{"codex":{"model-config":{"tiers":["fast"]}}}}
    assert select_model(capability,"codex","fast",config)[0:2]==("model-config","config")
    assert select_model(capability,"codex","fast",{})[0:2]==("model-meta","provider_metadata")


def test_history_precedes_name_heuristic_and_cli_default_only_if_no_catalog():
    capability=cap("codex",["a-neutral","z-flash"])
    history={("codex","a-neutral"):{"samples":4,"success_rate":0.9}}
    assert select_model(capability,"codex","fast",{},metrics=history)[0:2]==("a-neutral","history")
    assert select_model(capability,"codex","fast",{})[0:2]==("z-flash","heuristic")
    assert select_model(cap("codex",[]),"codex","fast",{})[1]=="cli_default"


def test_router_prefers_explicit_available_model_and_keeps_independence():
    router=ModelRouter({"agy":cap("agy",["model-a"]),"codex":cap("codex",["model-b"])},
        {"roles":{"specification_validator":{"preferred_providers":["agy","codex"]}}})
    route=router.route("specification_validator",author_provider="agy")
    assert route.provider=="codex" and route.model=="model-b" and route.independence is True
    assert route.tier_source=="heuristic" and "tier_source=heuristic" in format_route("specification_validator",route)


def test_unavailable_preferred_provider_falls_back():
    router=ModelRouter({"opencode":cap("opencode",["a"],available=False),"codex":cap("codex",["b"])})
    assert router.route("coder").provider=="codex"


def test_harness_detects_but_does_not_run_suggestions(tmp_path, monkeypatch):
    (tmp_path/"pyproject.toml").write_text('[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n[tool.ruff]\n[tool.mypy]\n')
    (tmp_path/"tests").mkdir()
    harness=VerificationHarness(tmp_path)
    suggestions=harness.detect()
    assert suggestions["build"] and suggestions["tests"] and suggestions["syntax"] and suggestions["lint"] and suggestions["type"]
    assert harness.run()==[]


def test_failed_build_and_requirement_check_remain_failed(tmp_path):
    fail=["python","-c","raise SystemExit(1)"]
    harness=VerificationHarness(tmp_path,{"build":[{"name":"build","command":fail}],
        "requirements":[{"requirement_id":"FR-018","checks":[{"name":"FR-018 test","command":fail}]}]})
    results=harness.run()
    assert all(not result.success and result.status=="FAIL" for result in results)
    assert harness.requirement_results[0].status=="FAIL"
    assert not final_verification_pass(results,harness.requirement_results)


def test_requirement_without_check_is_blocked(tmp_path):
    harness=VerificationHarness(tmp_path,{"requirements":[{"requirement_id":"FR-018","checks":[]}]})
    assert harness.run_requirements()[0].status=="BLOCKED"
    assert not final_verification_pass([],harness.requirement_results)


def test_failed_regression_cannot_complete_tdd_task():
    task=TDDTask("T001",["FR-001"],["AC-001"])
    gate=TDDGate(task)
    assert gate.red("PASS","EXPECTED_FAILURE")
    assert gate.green(True)=="PASS"
    assert gate.refactor(True,False) is False
    assert task.phase==TDDPhase.REGRESSION_VERIFY
    assert not task.complete()


def test_doctor_warns_on_cli_default_heuristics_and_missing_gates(tmp_path,monkeypatch,capsys):
    from argparse import Namespace
    from orchestrator import cli
    from orchestrator.config.models import ProviderCapabilities
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli,"discover_all",lambda root:{"agy":ProviderCapabilities("agy",True,"1",[],supports_model_selection=True)})
    monkeypatch.setattr(cli,"write_selection_report",lambda *args:None)
    cli.doctor(Namespace(config="orchestrator.yaml"))
    output=capsys.readouterr().out
    assert "CLI default" in output and "missing verification gate" in output
    assert "syntax check" in output and "type checking" in output and "lint" in output


def test_dry_run_warns_when_model_cannot_be_resolved(tmp_path,monkeypatch,capsys):
    from argparse import Namespace
    from orchestrator import cli
    from orchestrator.agents.router import ModelRouter
    monkeypatch.chdir(tmp_path)
    capability=cap("agy",[])
    monkeypatch.setattr(cli,"_router",lambda root,config:(ModelRouter({"agy":capability}),{"agy":capability}))
    cli.run(Namespace(config="orchestrator.yaml",feature="Feature de teste",feature_file=None,coder_provider=None,dry_run=True))
    output=capsys.readouterr().out
    assert "tier_source=cli_default" in output and "WARNING: model not explicitly resolved" in output


def test_dry_run_workflow_summary_displays_canonical_stages(tmp_path,monkeypatch,capsys):
    from argparse import Namespace
    from orchestrator import cli
    from orchestrator.agents.router import ModelRouter
    monkeypatch.chdir(tmp_path)
    capability=cap("agy",[])
    monkeypatch.setattr(cli,"_router",lambda root,config:(ModelRouter({"agy":capability}),{"agy":capability}))
    cli.run(Namespace(config="orchestrator.yaml",feature="Feature de teste",feature_file=None,coder_provider=None,dry_run=True))
    output=capsys.readouterr().out
    expected_summary = "Workflow: Constitution → Spec → Clarify → Checklist → Plan → Tasks → Analysis (speckit-analyze) → TDD (Red/Green/Refactor/Review) → Converge → Final verification"
    assert expected_summary in output


def test_json_syntax_yaml_loads_without_pyyaml_and_legacy_tests_survive(tmp_path):
    from orchestrator.config.loader import load_config
    (tmp_path/"orchestrator.yaml").write_text(json.dumps({"verification":{"tests":["python -m pytest -q"]}}))
    (tmp_path/"models.yaml").write_text(json.dumps({"models":{"agy":{"model-a":{"tiers":["balanced"]}}}}))
    cfg=load_config(tmp_path/"orchestrator.yaml")
    assert cfg.verification["tests"] and cfg.models["models"]["agy"]["model-a"]["tiers"]==["balanced"]


def test_configure_does_not_overwrite_existing_files(tmp_path,monkeypatch):
    from orchestrator import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path/"orchestrator.yaml").write_text("existing")
    (tmp_path/"models.yaml").write_text("existing")
    cli.configure(None)
    assert (tmp_path/"orchestrator.yaml").read_text()=="existing"
    assert (tmp_path/"models.yaml").read_text()=="existing"


def test_configure_generates_reviewable_files_from_discovery(tmp_path,monkeypatch):
    from orchestrator import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli,"discover_all",lambda root:{"agy":cap("agy",["model-fast"],
        [ModelCapabilities("agy","model-fast",display_name="Fast model")])})
    cli.configure(None)
    from orchestrator.config.loader import load_config
    cfg=load_config(tmp_path/"orchestrator.yaml")
    assert cfg.verification["requirements"]==[]
    assert cfg.models["models"]["agy"]["model-fast"]["tiers"]==["fast"]
