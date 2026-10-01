import json
import pytest

from orchestrator.config.models import AgentResult, ProviderCapabilities
from orchestrator.agents.runner import AgentRunner
from orchestrator.agents.router import ModelRouter
from orchestrator.agents.independence import is_family_independent, model_family
from orchestrator.workflow.protection import snapshot_protected_paths, verify_protected_paths
from orchestrator.verification.harness import VerificationHarness, VerificationResult
from orchestrator.workflow.task_adapter import parse_speckit_tasks, TaskContractError
from orchestrator.tdd import execute_tdd_task
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase
from orchestrator.providers.agy import AgyProvider


# ---------------------------------------------------------------------------
# 1. MODEL_CATALOG_UNAVAILABLE
# ---------------------------------------------------------------------------
def test_model_catalog_unavailable_fail_closed(tmp_path):
    class FakeProvider:
        name = "codex"
        def list_models(self):
            return ["gpt-6-sol"]
        def run(self, *args, **kwargs):
            return AgentResult("codex", "gpt-99-unknown", "coder", True, stdout="ok")

    provider = FakeProvider()
    capabilities = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol", "gpt-99-unknown"], True, True, True, True, False, True, False)
    }
    router = ModelRouter(capabilities)
    runner = AgentRunner({"codex": provider}, router)

    result = runner.run("coder", "do work", cwd=tmp_path, override_provider="codex", override_model="gpt-99-unknown")
    assert not result.success
    assert "MODEL_CATALOG_UNAVAILABLE" in result.error
    assert "gpt-99-unknown" in result.error


# ---------------------------------------------------------------------------
# 2 & 3. Gate Integrity: Protected Paths
# ---------------------------------------------------------------------------
def test_integrity_check_detects_conftest_tampering(tmp_path):
    # Snapshot before worker
    before = snapshot_protected_paths(tmp_path)
    # Worker modifies or creates conftest.py
    conftest = tmp_path / "tests" / "conftest.py"
    conftest.parent.mkdir(parents=True, exist_ok=True)
    conftest.write_text("# tampered\n")
    # Verify after worker
    violations = verify_protected_paths(tmp_path, before)
    assert len(violations) > 0
    assert any("conftest.py" in v for v in violations)


def test_integrity_check_detects_git_hooks_tampering(tmp_path):
    before = snapshot_protected_paths(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    violations = verify_protected_paths(tmp_path, before)
    assert len(violations) > 0
    assert any(".git/hooks/pre-commit" in v for v in violations)


# ---------------------------------------------------------------------------
# 4, 5, 6. RED Classifier Determinism
# ---------------------------------------------------------------------------
def test_red_classifier_exit_code_2_to_5(tmp_path):
    harness = VerificationHarness(tmp_path)
    for exit_code in (2, 3, 4, 5):
        res = harness.run_red(["python", "-c", f"raise SystemExit({exit_code})"])
        assert res.classification == "INVALID_TEST"
        assert res.status == "INVALID_TEST"


def test_red_classifier_skipped_and_xfailed(tmp_path):
    harness = VerificationHarness(tmp_path)
    skipped_res = harness.run_red(["python", "-c", "print('collected 1 item'); print('1 skipped'); raise SystemExit(0)"])
    assert skipped_res.classification == "INVALID_TEST"

    xfailed_res = harness.run_red(["python", "-c", "print('collected 1 item'); print('1 xfailed'); raise SystemExit(0)"])
    assert xfailed_res.classification == "INVALID_TEST"


def test_red_classifier_modulenotfound_internal_vs_external(tmp_path):
    harness = VerificationHarness(tmp_path)
    # 1. Internal module declared in allowed_files
    cmd_internal = ["python", "-c", "print('collected 1 item'); print(\"ModuleNotFoundError: No module named 'filters.butterworth'\"); raise SystemExit(1)"]
    res_int = harness.run_red(cmd_internal, allowed_files=["filters/butterworth.py"])
    assert res_int.classification == "EXPECTED_FAILURE"

    # 2. External module not declared in allowed_files, expected_failure, or workspace
    cmd_external = ["python", "-c", "print('collected 1 item'); print(\"ModuleNotFoundError: No module named 'scipy'\"); raise SystemExit(1)"]
    res_ext = harness.run_red(cmd_external, allowed_files=["feature.py"])
    assert res_ext.classification in ("INVALID_TEST", "INFRASTRUCTURE_FAILURE")
    assert res_ext.classification != "EXPECTED_FAILURE"


# ---------------------------------------------------------------------------
# 7, 8, 9. Author-Validator Family Independence
# ---------------------------------------------------------------------------
def test_is_family_independent_rejects_same_family_in_numeric_tasks():
    # Same family (gpt)
    ok, reason = is_family_independent(["gpt-6-sol"], "gpt-6-astra", author_role="test_designer", validator_role="test_validator", numeric_sensitive=True)
    assert ok is False
    assert "gpt" in reason

    # Same family (claude)
    ok, reason = is_family_independent(["claude-sonnet-4-6"], "claude-opus-4-6", author_role="coder", validator_role="code_reviewer", numeric_sensitive=True)
    assert ok is False
    assert "claude" in reason

    # Different families (gpt vs claude)
    ok, reason = is_family_independent(["gpt-6-sol"], "claude-sonnet-4-6", author_role="test_designer", validator_role="test_validator", numeric_sensitive=True)
    assert ok is True
    assert reason == "independent"


def test_router_skips_same_family_and_fails_closed_when_insufficient():
    # Router with only Codex (gpt) and AGY (claude)
    caps = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol", "gpt-6-astra"], True, True, True, True, False, True, False),
        "agy": ProviderCapabilities("agy", True, "1.0", ["claude-sonnet-4-6"], True, True, True, True, False, True, False),
    }
    router = ModelRouter(caps)

    # When coder is claude-sonnet-4-6, code_reviewer should NOT pick claude
    route = router.route("code_reviewer", author_models=["claude-sonnet-4-6"], author_role="coder", numeric_sensitive=True)
    assert route.provider == "codex"
    assert model_family(route.model) != "claude"

    # When all available candidates collide with author family
    codex_only_caps = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol", "gpt-6-astra"], True, True, True, True, False, True, False)
    }
    codex_router = ModelRouter(codex_only_caps)
    route_collided = codex_router.route("test_validator", author_models=["gpt-6-sol"], author_role="test_designer", numeric_sensitive=True)
    assert route_collided.provider == "unavailable"
    assert "INSUFFICIENT_INDEPENDENT_PROVIDERS" in route_collided.reason


def test_numeric_sensitive_enforces_test_validator_not_equal_coder():
    # 1. Direct pair check: test_validator (gemini) vs coder (gemini) MUST be rejected when numeric_sensitive=True
    ok, reason = is_family_independent(
        [("gemini-3.8-flash-high", "test_validator")],
        "gemini-3.8-flash-medium",
        validator_role="coder",
        numeric_sensitive=True,
    )
    assert ok is False
    assert "gemini" in reason

    # 2. Both test_designer and test_validator present in author_models:
    # test_designer = Sol (gpt), test_validator = MiMo (mimo)
    authors = [("gpt-6-sol", "test_designer"), ("opencode-go/mimo-v2.6-pro", "test_validator")]

    # Coder candidate Astra (gpt) -> REJECTED (collides with test_designer)
    ok, reason = is_family_independent(authors, "gpt-6-astra", validator_role="coder", numeric_sensitive=True)
    assert ok is False
    assert "gpt" in reason

    # Coder candidate MiMo (mimo) -> REJECTED (collides with test_validator)
    ok, reason = is_family_independent(authors, "opencode-go/mimo-v2.5-pro", validator_role="coder", numeric_sensitive=True)
    assert ok is False
    assert "mimo" in reason

    # Coder candidate Sonnet (claude) -> ACCEPTED (independent of both!)
    ok, reason = is_family_independent(authors, "claude-sonnet-4-6", validator_role="coder", numeric_sensitive=True)
    assert ok is True
    assert reason == "independent"

    # Coder candidate Kimi (kimi) -> ACCEPTED (independent of both!)
    ok, reason = is_family_independent(authors, "opencode-go/kimi-k3", validator_role="coder", numeric_sensitive=True)
    assert ok is True
    assert reason == "independent"

    # 3. When numeric_sensitive=False:
    # test_validator (gemini) and coder (gemini) is PERMITTED (advisory collision only)
    ok, reason = is_family_independent(
        [("gemini-3.8-flash-high", "test_validator")],
        "gemini-3.8-flash-medium",
        validator_role="coder",
        numeric_sensitive=False,
    )
    assert ok is True


def test_router_enforces_test_validator_diff_coder_in_numeric():
    # Router with 3 providers: Codex (gpt), OpenCode (mimo), AGY (claude)
    caps = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol"], True, True, True, True, False, supports_file_editing=True, supports_shell=True),
        "opencode": ProviderCapabilities("opencode", True, "1.0", ["opencode-go/mimo-v2.6-pro"], True, True, True, True, False, supports_file_editing=True, supports_shell=True),
        "agy": ProviderCapabilities("agy", True, "1.0", ["claude-sonnet-4-6"], True, True, True, True, False, supports_file_editing=True, supports_shell=True),
    }
    router = ModelRouter(caps)

    # Given test_designer = gpt-6-sol and test_validator = opencode-go/mimo-v2.6-pro:
    authors = [("gpt-6-sol", "test_designer"), ("opencode-go/mimo-v2.6-pro", "test_validator")]

    # Routing coder with numeric_sensitive=True MUST NOT pick gpt or mimo -> must pick claude
    route = router.route("coder", author_models=authors, numeric_sensitive=True)
    assert route.provider == "agy"
    assert route.model == "claude-sonnet-4-6"
    assert model_family(route.model) not in ("gpt", "mimo")

    # If only Codex (gpt) and OpenCode (mimo) were available, routing coder must fail closed
    restricted_caps = {
        "codex": ProviderCapabilities("codex", True, "1.0", ["gpt-6-sol"], True, True, True, True, False, supports_file_editing=True, supports_shell=True),
        "opencode": ProviderCapabilities("opencode", True, "1.0", ["opencode-go/mimo-v2.6-pro"], True, True, True, True, False, supports_file_editing=True, supports_shell=True),
    }
    restricted_router = ModelRouter(restricted_caps)
    route_fail = restricted_router.route("coder", author_models=authors, numeric_sensitive=True)
    assert route_fail.provider == "unavailable"
    assert "INSUFFICIENT_INDEPENDENT_PROVIDERS" in route_fail.reason


# ---------------------------------------------------------------------------
# 10. Task Parser: Numeric Keyword Lint
# ---------------------------------------------------------------------------
def test_task_parser_rejects_numeric_keywords_when_not_sensitive():
    markdown_bad = (
        "- [ ] T001 Implement bandpass filter for EMG signal\n"
        '<!-- harness-task {"requirements":["FR1"],"acceptance_criteria":["AC1"],"test_type":"UNIT","tdd_phases":["RED","GREEN","REFACTOR"],"plan_decisions":[],"dependencies":[],"allowed_files":["filter.py"],"numeric_sensitive":false} -->\n'
    )
    with pytest.raises(TaskContractError) as exc:
        parse_speckit_tasks(markdown_bad)
    assert "MISCLASSIFIED_TASK" in str(exc.value)

    markdown_good = (
        "- [ ] T001 Implement bandpass filter for EMG signal\n"
        '<!-- harness-task {"requirements":["FR1"],"acceptance_criteria":["AC1"],"test_type":"UNIT","tdd_phases":["RED","GREEN","REFACTOR"],"plan_decisions":[],"dependencies":[],"allowed_files":["filter.py"],"numeric_sensitive":true} -->\n'
    )
    tasks = parse_speckit_tasks(markdown_good)
    assert len(tasks) == 1
    assert tasks[0]["numeric_sensitive"] is True


# ---------------------------------------------------------------------------
# 11. Scientific Fixture Protection against TEST_TAMPERING
# ---------------------------------------------------------------------------
def test_scientific_fixture_tampering_detected(tmp_path):
    TASK_CMD = ["python", "-m", "pytest", "-q", "tests/test_signal.py::test_signal"]
    REG_CMD = ["python", "-m", "pytest", "-q"]
    VAL = json.dumps({"status": "PASS", "issues": [], "summary": "ok"})

    fixture_file = tmp_path / "tests" / "fixtures" / "emg_sample.npy"
    fixture_file.parent.mkdir(parents=True, exist_ok=True)
    fixture_file.write_bytes(b"initial_clean_signal_data")

    class RunnerWithFixtureTamper:
        def __init__(self, root):
            self.root = root
            self.roles = []
        def run(self, role, prompt, **kwargs):
            self.roles.append(role)
            if role == "test_designer" and "RED:" in prompt:
                test_p = self.root / "tests" / "test_signal.py"
                test_p.parent.mkdir(parents=True, exist_ok=True)
                test_p.write_text("def test_signal(): assert False\n")
                raw = json.dumps({
                    "task_id": "T1",
                    "requirement_ids": ["FR1"],
                    "acceptance_criteria_ids": ["AC1"],
                    "created_tests": ["tests/test_signal.py::test_signal"],
                    "test_commands": [TASK_CMD],
                    "fixture_files": ["tests/fixtures/emg_sample.npy"],
                })
            elif role == "coder":
                # Coder tampers with fixture file!
                (self.root / "tests" / "fixtures" / "emg_sample.npy").write_bytes(b"tampered_signal")
                (self.root / "signal.py").write_text("DATA=1\n")
                raw = "done"
            elif role in ("test_validator", "code_reviewer"):
                raw = VAL
            else:
                raw = "analysis" if role == "test_designer" else "done"
            return AgentResult("fake", "fake-model", role, True, stdout=raw)

    class Harness:
        def run_red(self, command, **kwargs):
            return VerificationResult(command, False, 1, "collected 1 item; FAILED tests/test_signal.py::test_signal - AssertionError", "", 0.1, "EXPECTED_FAILURE", cause="linked assertion")
        def run_command(self, command, **kwargs):
            return VerificationResult(command, True, 0, "", "", 0.1, "PASS")

    runner = RunnerWithFixtureTamper(tmp_path)
    harness = Harness()
    task = TDDTask("T1", ["FR1"], ["AC1"])
    task_data = {"id": "T1", "allowed_files": ["signal.py"], "numeric_sensitive": True}

    execute_tdd_task(
        task, runner, harness, tmp_path,
        regression_commands=[REG_CMD],
        workflow_dir=tmp_path / "runs",
        task_data=task_data,
    )
    assert task.phase == TDDPhase.BLOCKED
    assert task.evidence["test_tampering_detected"] is True
    assert "tests/fixtures/emg_sample.npy" in task.evidence["fixture_tampering_files"]


# ---------------------------------------------------------------------------
# 12. REFACTOR as Deterministic No-op in Numeric Sensitive Tasks
# ---------------------------------------------------------------------------
def test_refactor_noop_numeric_when_no_snapshot_gate(tmp_path):
    TASK_CMD = ["python", "-m", "pytest", "-q", "tests/test_dsp.py::test_dsp"]
    REG_CMD = ["python", "-m", "pytest", "-q"]
    VAL = json.dumps({"status": "PASS", "issues": [], "summary": "ok"})

    class RunnerNoRefactor:
        def __init__(self, root):
            self.root = root
            self.roles = []
        def run(self, role, prompt, **kwargs):
            self.roles.append(role)
            if role == "test_designer" and "RED:" in prompt:
                test_p = self.root / "tests" / "test_dsp.py"
                test_p.parent.mkdir(parents=True, exist_ok=True)
                test_p.write_text("def test_dsp(): assert False\n")
                raw = json.dumps({
                    "task_id": "T1",
                    "requirement_ids": ["FR1"],
                    "acceptance_criteria_ids": ["AC1"],
                    "created_tests": ["tests/test_dsp.py::test_dsp"],
                    "test_commands": [TASK_CMD],
                })
            elif role == "coder":
                (self.root / "dsp.py").write_text("FILTER=True\n")
                raw = "done"
            elif role in ("test_validator", "code_reviewer"):
                raw = VAL
            else:
                raw = "analysis" if role == "test_designer" else "done"
            return AgentResult("fake", "fake-model", role, True, stdout=raw)

    class Harness:
        def __init__(self):
            self.commands = []
        def run_red(self, command, **kwargs):
            self.commands.append(("RED", command))
            return VerificationResult(command, False, 1, "collected 1 item; FAILED tests/test_dsp.py::test_dsp - AssertionError", "", 0.1, "EXPECTED_FAILURE", cause="linked assertion")
        def run_command(self, command, **kwargs):
            self.commands.append((kwargs.get("category"), command))
            return VerificationResult(command, True, 0, "", "", 0.1, "PASS")

    runner = RunnerNoRefactor(tmp_path)
    harness = Harness()
    task = TDDTask("T1", ["FR1"], ["AC1"])
    task_data = {"id": "T1", "allowed_files": ["dsp.py"], "numeric_sensitive": True, "snapshot_gate_registered": False}

    execute_tdd_task(
        task, runner, harness, tmp_path,
        regression_commands=[REG_CMD],
        workflow_dir=tmp_path / "runs",
        task_data=task_data,
        snapshot_gate_registered=False,
    )
    assert task.phase == TDDPhase.COMPLETE
    # refactorer worker must NOT have been called
    assert "refactorer" not in runner.roles
    assert task.evidence.get("refactor_noop_numeric") is True
    assert task.evidence.get("refactor_result") == "REFACTOR_NOOP_NUMERIC"
    # regression tests must have executed
    assert any(cmd[0] == "regression_tests" for cmd in harness.commands)


# ---------------------------------------------------------------------------
# 13. AGY Runner: Isolation Flag Passed When Discovered
# ---------------------------------------------------------------------------
def test_agy_disables_slash_commands_when_flag_available():
    provider = AgyProvider()
    provider._disable_slash_commands = True
    cmd = provider.build_command("test prompt", "coder", "claude-sonnet-4-6")
    assert "--disable-slash-commands" in cmd
