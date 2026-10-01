from argparse import Namespace
import contextlib
import io
from pathlib import Path

from orchestrator.agents.cost import CostAwareRouter
from orchestrator.agents.roles import ROLES
from orchestrator.config.loader import load_config
from orchestrator.config.models import ProviderCapabilities
import orchestrator.cli as cli
import orchestrator.doctor as doctor
from orchestrator.doctor import ProviderSmoke, SmokeCheck


def make_test_router():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "orchestrator.yaml")
    router, _ = cli._router(root, cfg)
    return router, cfg


def test_red_simple_selects_opencode_mimo_pro():
    router, _ = make_test_router()
    route = router.route("test_designer")
    assert route.provider == "opencode"
    assert route.model == "opencode-go/mimo-v2.6-pro"


def test_green_simple_selects_agy_gemini_flash_high():
    router, _ = make_test_router()
    route = router.route("coder", task={"task_complexity": "LOW"})
    assert route.provider == "agy"
    assert route.model == "gemini-3.8-flash-high"


def test_green_complex_escalates_candidate_priority():
    router, _ = make_test_router()
    route = router.route("coder", task={"task_complexity": "HIGH"})
    # Primary remains gemini-3.8-flash-high
    assert route.provider == "agy"
    assert route.model == "gemini-3.8-flash-high"

    # For high complexity tasks, Sol is prioritized ahead of economical alternatives for capability escalation
    models_order = [c["model"] for c in route.candidates if c.get("model")]
    assert "gpt-6-sol" in models_order
    assert "opencode-go/kimi-k3" in models_order
    assert models_order.index("gpt-6-sol") < models_order.index("opencode-go/kimi-k3")


def test_refactor_prefers_kimi():
    router, _ = make_test_router()
    route = router.route("refactorer")
    assert route.provider == "opencode"
    assert route.model == "opencode-go/kimi-k3"


def test_validator_with_agy_author_prefers_opencode_mimo():
    router, _ = make_test_router()
    route = router.route("specification_validator", author_provider="agy")
    assert route.provider == "opencode"
    assert route.model == "opencode-go/mimo-v2.6-pro"
    assert route.independence is True


def test_validator_with_codex_author_prefers_agy_flash():
    router, _ = make_test_router()
    route = router.route("specification_validator", author_provider="codex")
    assert route.provider == "agy"
    assert route.model == "gemini-3.8-flash-high"
    assert route.independence is True


def test_validator_with_opencode_author_prefers_agy_flash():
    router, _ = make_test_router()
    route = router.route("specification_validator", author_provider="opencode")
    assert route.provider == "agy"
    assert route.model == "gemini-3.8-flash-high"
    assert route.independence is True


def test_validator_never_uses_author_provider_when_independent_alternative_exists():
    router, _ = make_test_router()
    for author in ("agy", "codex", "opencode"):
        route = router.route("specification_validator", author_provider=author)
        assert route.provider != author
        assert route.independence is True


def test_provider_failure_causes_provider_fallback_not_intelligence_escalation():
    router, cfg = make_test_router()
    cost_router = CostAwareRouter(router, cfg.cost_optimization)
    base_route = router.route("coder")

    infra_failures = [
        {"provider": "agy", "model": "gemini-3.8-flash-high", "category": "PROVIDER_FAILURE"},
        {"provider": "agy", "model": "gemini-3.8-flash-high", "category": "TIMEOUT"},
        {"provider": "agy", "model": "gemini-3.8-flash-high", "category": "INFRASTRUCTURE_FAILURE"},
        {"provider": "agy", "model": "gemini-3.8-flash-high", "category": "QUOTA_EXHAUSTED"},
    ]
    assessment = cost_router.assess("coder", base_route, failures=infra_failures)
    assert assessment["previous_failure_reasons"] == []
    # No intelligence escalation occurred because failures were infrastructure/provider-related
    assert assessment["recommended_escalation_level"] == "CODING_ECONOMY"

    # Provider failure causes provider fallback in router (AGY unavailable -> OpenCode Kimi K3)
    fallback_route = router.route("coder", exclude={"agy"})
    assert fallback_route.provider == "opencode"
    assert fallback_route.model == "opencode-go/kimi-k3"

    # Secondary fallback when both AGY and OpenCode are unavailable -> Codex Sol
    secondary_fallback = router.route("coder", exclude={"agy", "opencode"})
    assert secondary_fallback.provider == "codex"
    assert secondary_fallback.model == "gpt-6-sol"


def test_real_capability_failure_unlocks_escalation_ladder():
    router, cfg = make_test_router()
    cost_router = CostAwareRouter(router, cfg.cost_optimization)
    base_route = router.route("coder")

    cap_failures = [
        {"provider": "opencode", "model": "opencode-go/qwen3.8-flash", "category": "GREEN_IMPLEMENTATION_FAILURE"},
        {"provider": "opencode", "model": "opencode-go/qwen3.8-flash", "category": "MODEL_CAPABILITY_FAILURE"},
    ]
    assessment = cost_router.assess("coder", base_route, failures=cap_failures)
    assert len(assessment["previous_failure_reasons"]) == 2
    for candidate in assessment["candidates"]:
        if candidate["model"] == "opencode-go/qwen3.8-flash":
            assert candidate["eligibility"] == "ineligible"


def test_astra_not_selected_in_normal_routing():
    router, _ = make_test_router()
    for role in ROLES:
        route = router.route(role)
        assert route.model != "gpt-6-astra"
        astra_candidates = [c for c in route.candidates if c.get("model") == "gpt-6-astra"]
        for cand in astra_candidates:
            assert cand["score_breakdown"].get("extreme_fallback") == -100.0


def test_doctor_smoke_check_renders_exactly_once(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "discover_all", lambda r: {"agy": ProviderCapabilities("agy", cli_available=True)})
    monkeypatch.setattr(cli, "write_selection_report", lambda *args: None)

    check = SmokeCheck("PASS", ["agy", "--print", "AGY_SMOKE_OK"], 0, "PASS (plain stdout)", "PASS (contains AGY_SMOKE_OK)")
    mock_smoke = {"agy": ProviderSmoke("available", "supported", check, check)}
    monkeypatch.setattr(doctor, "live_smoke_tests", lambda *args: mock_smoke)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.doctor(Namespace(config=str(root / "orchestrator.yaml"), live=True, verbose=True))

    output = buf.getvalue()
    assert output.count("Live provider smoke tests (explicitly requested):") == 1


def test_stage_registry_analysis_uses_consistency_agent_and_speckit_analyze():
    from orchestrator.workflow.stages import STAGE_REGISTRY

    stage = STAGE_REGISTRY.get("ANALYSIS")
    assert stage.role == "consistency_agent"
    assert stage.skill_name == "speckit-analyze"
    assert stage.mutability == "read_only"
    assert stage.prerequisites == ("TASKS",)


def test_cross_artifact_validator_is_deprecated_alias_for_consistency_agent():
    from orchestrator.agents.roles import ROLE_ALIASES, ROLES

    assert ROLE_ALIASES.get("cross_artifact_validator") == "consistency_agent"
    legacy_role = ROLES["cross_artifact_validator"]
    assert legacy_role.deprecated is True
    assert legacy_role.alias_for == "consistency_agent"

    router, _ = make_test_router()
    route_canonical = router.route("consistency_agent")
    route_alias = router.route("cross_artifact_validator")

    assert route_alias.provider == route_canonical.provider
    assert route_alias.model == route_canonical.model
    assert route_alias.tier == route_canonical.tier

    explained = router.explain("cross_artifact_validator")
    assert explained["alias_for"] == "consistency_agent"
    assert explained["role"] == "cross_artifact_validator"


def test_cli_and_selection_report_do_not_duplicate_cross_artifact_validator(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "orchestrator.yaml")
    router, caps = cli._router(root, cfg)

    doctor.write_selection_report(tmp_path, caps, router)
    report_text = (tmp_path / ".orchestrator" / "model-selection.md").read_text()

    assert "`consistency_agent`" in report_text
    assert "`cross_artifact_validator`" not in report_text

    import inspect
    from orchestrator.cli import run
    run_source = inspect.getsource(run)
    assert '"consistency_agent"' in run_source
    assert '"cross_artifact_validator"' not in run_source


def test_workflow_driver_executes_consistency_agent_and_no_legacy_cross_validator(tmp_path):
    from types import SimpleNamespace
    import json
    import pytest
    from orchestrator.workflow.driver import WorkflowBlocked, run_sdd_workflow
    from orchestrator.config.models import Config
    from orchestrator.storage.sqlite import StateStore
    from orchestrator.workflow.resume import WorkspaceFingerprint

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    store = StateStore(root / ".orchestrator" / "state" / "test.sqlite3")
    wid = store.create_workflow("feature")
    folder = root / ".orchestrator" / "runs" / wid
    folder.mkdir(parents=True)

    feature_dir = root / "specs" / "feature"
    feature_dir.mkdir(parents=True)
    memory = root / ".specify" / "memory"
    memory.mkdir(parents=True)
    (root / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": "specs/feature"}))
    (memory / "constitution.md").write_text("# Constitution\n\nPython governs deterministic stage gates.\n")
    (feature_dir / "spec.md").write_text("# Specification\n\nFR-001: List providers. AC-001: Return names.\n")
    (feature_dir / "plan.md").write_text("# Plan\n\nD-001: Use the configured provider registry.\n")
    (feature_dir / "tasks.md").write_text(
        '# Tasks\n\n- [ ] T001 List providers\n'
        '  <!-- harness-task {"requirements":["FR-001"],"acceptance_criteria":["AC-001"],'
        '"plan_decisions":["D-001"],"dependencies":[],"test_type":"UNIT",'
        '"allowed_files":["src/providers.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->\n'
    )
    (feature_dir / "checklists").mkdir()
    (feature_dir / "checklists" / "requirements.md").write_text("# Requirements\n\n- [x] Testable requirements.\n")

    fp = WorkspaceFingerprint(root).capture(wid)
    for stage in ("CONSTITUTION_VALIDATED", "SPEC_VALIDATED", "CLARIFICATION_COMPLETE", "CHECKLIST_COMPLETE", "PLAN_VALIDATED", "TASKS_VALIDATED"):
        store.create_checkpoint(wid, f"{stage}:-:1", stage, fp, None)

    executed_roles = []

    class MockRunner:
        def run(self, role, prompt, *args, **kwargs):
            executed_roles.append(role)
            return SimpleNamespace(
                provider="codex",
                model="gpt-6-luna",
                success=True,
                stdout="## Speckit Analyze Report\nNo critical findings.",
                stderr="",
                error=None,
            )

        def run_skill(self, role, provider, model, skill_name, prompt, cwd, **kwargs):
            executed_roles.append(role)
            (folder / "analysis-report.md").write_text("# SpecKit Analyze Report\n\nCritical Issues Count: 0\n")
            return SimpleNamespace(
                provider="codex",
                model="gpt-6-luna",
                success=True,
                stdout='{"conversation_id":"fake-analysis","status":"SUCCESS","response":""}',
                stderr="",
                error=None,
            )

    class MockHarness:
        def run(self, *args, **kwargs):
            return []
        requirement_results = []

    cfg = Config()
    with pytest.raises(WorkflowBlocked, match="TDD task T001 blocked"):
        run_sdd_workflow(
            "feature", root, MockRunner(), MockHarness(), cfg, folder, store=store, resume=True,
            gate_callback=lambda stage, role, *args, **kwargs: role != "test_designer",
        )

    assert executed_roles == ["consistency_agent"]
    assert "cross_artifact_validator" not in executed_roles

    checkpoints = [row["stage"] for row in store.checkpoints(wid)]
    assert "ANALYSIS_COMPLETE" in checkpoints
    assert "CROSS_VALIDATED" not in checkpoints


def test_all_roles_primary_mappings():
    router, _ = make_test_router()
    expected_primaries = {
        "constitution": ("codex", "gpt-6-sol"),
        "constitution_validator": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "specification": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "specification_validator": ("agy", "gemini-3.8-flash-high"),
        "clarifier_agent": ("agy", "gemini-3.8-flash-medium"),
        "requirements_reviewer": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "planning": ("codex", "gpt-6-sol"),
        "plan_validator": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "tasks": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "tasks_validator": ("agy", "gemini-3.8-flash-high"),
        "consistency_agent": ("codex", "gpt-6-sol"),
        "test_designer": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "test_validator": ("agy", "gemini-3.8-flash-high"),
        "coder": ("agy", "gemini-3.8-flash-high"),
        "refactorer": ("opencode", "opencode-go/kimi-k3"),
        "code_reviewer": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "convergence_agent": ("opencode", "opencode-go/mimo-v2.6-pro"),
        "final_reviewer": ("codex", "gpt-6-sol"),
    }
    for role, (exp_provider, exp_model) in expected_primaries.items():
        route = router.route(role)
        assert route.provider == exp_provider, f"Role {role} expected provider {exp_provider}, got {route.provider}"
        assert route.model == exp_model, f"Role {role} expected model {exp_model}, got {route.model}"


def test_codex_roles_have_cross_provider_first_fallback():
    router, _ = make_test_router()
    codex_roles = ["constitution", "planning", "consistency_agent", "final_reviewer"]
    for role in codex_roles:
        primary = router.route(role)
        assert primary.provider == "codex"
        assert primary.model == "gpt-6-sol"

        # First fallback MUST NOT belong to the same provider Codex
        fb1 = router.route(role, exclude={"codex"})
        assert fb1.provider == "agy", f"Role {role} first fallback provider must be agy, got {fb1.provider}"
        assert fb1.model == "claude-sonnet-4-6", f"Role {role} first fallback model must be claude-sonnet-4-6, got {fb1.model}"

        # Secondary fallback
        fb2 = router.route(role, exclude={"codex", "agy"})
        assert fb2.provider == "opencode"
        assert fb2.model == "opencode-go/mimo-v2.6-pro"


def test_tdd_phases_roles_separation():
    router, _ = make_test_router()
    # test_designer creates RED
    red_route = router.route("test_designer")
    assert red_route.provider == "opencode"
    assert red_route.model == "opencode-go/mimo-v2.6-pro"

    # coder implements GREEN
    green_route = router.route("coder")
    assert green_route.provider == "agy"
    assert green_route.model == "gemini-3.8-flash-high"

    # refactorer refactors
    refactor_route = router.route("refactorer")
    assert refactor_route.provider == "opencode"
    assert refactor_route.model == "opencode-go/kimi-k3"


def test_analyze_uses_consistency_agent_never_test_designer():
    from orchestrator.workflow.stages import STAGE_REGISTRY

    analysis_stage = STAGE_REGISTRY.get("ANALYSIS")
    assert analysis_stage is not None
    assert analysis_stage.role == "consistency_agent"
    assert analysis_stage.role != "test_designer"
    assert analysis_stage.skill_name == "speckit-analyze"

