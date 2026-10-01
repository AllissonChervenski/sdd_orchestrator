import json
from types import SimpleNamespace
import pytest

from orchestrator.agents.roles import ROLES
from orchestrator.agents.runner import AgentRunner, load_prompt
from orchestrator.config.models import AgentResult
from orchestrator.interactive import InteractiveGate
from orchestrator.workflow.driver import _call_gate


class DummyProvider:
    def __init__(self, name="codex", response_text=None):
        self.name = name
        self.response_text = response_text or '{"status": "PASS", "summary": "Valid", "issues": []}'
        self.calls = []

    def run(self, prompt, role, model, cwd, timeout=None, permissions=None):
        self.calls.append({
            "prompt": prompt,
            "role": role,
            "model": model,
            "cwd": cwd,
            "timeout": timeout,
            "permissions": permissions,
        })
        return AgentResult(
            provider=self.name,
            model=model or "dummy-model",
            role=role,
            success=True,
            stdout=self.response_text,
            stderr="",
            duration=0.1,
            usage={},
        )


class DummyRouter:
    def __init__(self, provider="codex", model="gpt-6-luna"):
        self.provider = provider
        self.model = model

    def route(self, role, **kwargs):
        return SimpleNamespace(
            provider=self.provider,
            model=self.model,
            independence="different_provider",
            selection_mode="role_binding",
            reason=None,
        )


# 1. constitution_validator recebe texto real da Constitution
def test_1_constitution_validator_receives_real_constitution_text(tmp_path):
    constitution_file = tmp_path / "constitution.md"
    real_content = "# SpecKit Constitution\nPrinciple 1: Determinism.\nPrinciple 2: TDD."
    constitution_file.write_text(real_content, encoding="utf-8")

    prompt = load_prompt("constitution_validator", feature="my-feature", artifact=constitution_file)
    assert real_content in prompt
    assert "CONSTITUTION TO VALIDATE:\n# SpecKit Constitution\nPrinciple 1: Determinism." in prompt


# 2. artifacts_supplied aparece no stage gate
def test_2_artifacts_supplied_appears_in_stage_gate(tmp_path):
    gate_events = []
    def gate_callback(stage, role, files, commands, attempt=1, artifacts=()):
        gate_events.append({"stage": stage, "role": role, "artifacts": list(artifacts)})
        return True

    constitution_rel = ".specify/memory/constitution.md"
    ok = _call_gate(gate_callback, "CONSTITUTION_VALIDATE", "constitution_validator", [], [], artifacts=[constitution_rel])
    assert ok is True
    assert len(gate_events) == 1
    assert gate_events[0]["stage"] == "CONSTITUTION_VALIDATE"
    assert gate_events[0]["artifacts"] == [constitution_rel]


# 3. artifacts_supplied permanece no AGENT_CALL
def test_3_artifacts_supplied_remains_in_agent_call(tmp_path):
    gate = InteractiveGate(workspace=tmp_path, input_fn=lambda _: "continue")
    calls = []

    # First call stage gate
    gate.confirm("CONSTITUTION_VALIDATE", "constitution_validator", "codex", "gpt-6-luna", [], [], artifacts=[".specify/memory/constitution.md"])

    # Intercept AGENT_CALL via on_call
    def on_call(role, provider, model, task_id, files, outputs, artifacts=()):
        calls.append({"role": role, "artifacts": list(artifacts)})
        return gate.confirm("AGENT_CALL", role, provider, model, files, outputs, task_id=task_id, artifacts=artifacts)

    provider = DummyProvider()
    runner = AgentRunner({"codex": provider}, DummyRouter())
    runner.on_call = on_call
    runner.set_stage_context("CONSTITUTION_VALIDATE", "constitution_validator", [".specify/memory/constitution.md"])

    res = runner.run("constitution_validator", "test prompt", cwd=tmp_path, artifacts=[".specify/memory/constitution.md"])
    assert res.success is True
    assert len(calls) == 1
    assert calls[0]["role"] == "constitution_validator"
    assert calls[0]["artifacts"] == [".specify/memory/constitution.md"]


# 4. prompt final contém o conteúdo real sob a seção CONSTITUTION TO VALIDATE:
def test_4_prompt_final_contains_real_content_under_section(tmp_path):
    real_constitution = "# LIVING CONSTITUTION\nCore Rule: Never tamper with tests."
    prompt = load_prompt("constitution_validator", feature="test-feature", artifact=real_constitution)
    assert "CONSTITUTION TO VALIDATE:\n# LIVING CONSTITUTION\nCore Rule: Never tamper with tests." in prompt


# 5. prompt final não contém artifact vazio (lança ValueError se vazio)
def test_5_prompt_final_rejects_empty_artifact():
    with pytest.raises(ValueError, match="Artifact content cannot be empty for role 'constitution_validator'"):
        load_prompt("constitution_validator", feature="test-feature", artifact="")

    with pytest.raises(ValueError, match="Artifact content cannot be empty for role 'constitution_validator'"):
        load_prompt("constitution_validator", feature="test-feature", artifact="   ")

    with pytest.raises(ValueError, match="Artifact content cannot be empty for role 'constitution_validator'"):
        load_prompt("constitution_validator", feature="test-feature", response="")


# 6. validator funciona com files_allowed=[]
def test_6_validator_works_with_files_allowed_empty(tmp_path):
    gate = InteractiveGate(workspace=tmp_path, input_fn=lambda _: "continue")
    summary_received = []

    def on_call(role, provider, model, task_id, files, outputs, artifacts=()):
        summary = gate._summary("AGENT_CALL", role, provider, model, files, outputs, task_id, artifacts=artifacts)
        summary_received.append(summary)
        return True

    provider = DummyProvider()
    runner = AgentRunner({"codex": provider}, DummyRouter())
    runner.on_call = on_call

    validator_roles = [
        "constitution_validator",
        "specification_validator",
        "plan_validator",
        "tasks_validator",
        "test_validator",
        "code_reviewer",
        "final_reviewer",
    ]

    for role in validator_roles:
        res = runner.run(role, f"prompt for {role}", cwd=tmp_path, allowed_paths=[], artifacts=["dummy.md"])
        assert res.success is True
        assert provider.calls[-1]["permissions"] == "read"

    assert len(summary_received) == len(validator_roles)
    for summary in summary_received:
        assert summary["files_allowed"] == []


# 7. specification_validator recebe spec.md
def test_7_specification_validator_receives_spec_md(tmp_path):
    spec_path = tmp_path / "spec.md"
    spec_content = "# Feature Specification\n## FR-001\nThe system shall provide summary."
    spec_path.write_text(spec_content, encoding="utf-8")

    prompt = load_prompt("specification_validator", feature="provider-summary", artifact=spec_path)
    assert "SPECIFICATION TO VALIDATE:" in prompt
    assert spec_content in prompt


# 8. plan_validator recebe plan.md
def test_8_plan_validator_receives_plan_md(tmp_path):
    plan_path = tmp_path / "plan.md"
    plan_content = "# Architecture Plan\nReuse existing providers without subprocess execution."
    plan_path.write_text(plan_content, encoding="utf-8")

    prompt = load_prompt("plan_validator", feature="provider-summary", artifact=plan_path)
    assert "PLAN TO VALIDATE:" in prompt
    assert plan_content in prompt


# 9. tasks_validator recebe tasks.md
def test_9_tasks_validator_receives_tasks_md(tmp_path):
    tasks_path = tmp_path / "tasks.md"
    tasks_content = "# Tasks\n- [ ] T001: Implement provider-summary"
    tasks_path.write_text(tasks_content, encoding="utf-8")

    prompt = load_prompt("tasks_validator", feature="provider-summary", artifact=tasks_path)
    assert "TASKS TO VALIDATE:" in prompt
    assert tasks_content in prompt


# 10. test_validator recebe testes/contexto
def test_10_test_validator_receives_tests_context():
    test_context = {"tests/test_feature.py": "def test_summary(): assert True"}
    prompt = load_prompt("test_validator", task="T001", artifact=json.dumps(test_context))
    assert "TESTS / ARTIFACT:" in prompt
    assert "def test_summary(): assert True" in prompt


# 11. code_reviewer recebe diff/evidências
def test_11_code_reviewer_receives_diff_and_evidence():
    evidence = {
        "task": "T001",
        "red_test_files": ["tests/test_feature.py"],
        "production_files_changed": ["orchestrator/cli.py"],
    }
    prompt = load_prompt("code_reviewer", task="T001", artifact=evidence)
    assert "EVIDENCE / ARTIFACT:" in prompt
    assert "orchestrator/cli.py" in prompt


# 12. final_reviewer recebe artifacts finais
def test_12_final_reviewer_receives_final_artifacts():
    final_payload = {
        "traceability": [{"requirement": "FR-001", "status": "PASS"}],
        "verification": [{"name": "pytest", "status": "PASS"}],
    }
    prompt = load_prompt("final_reviewer", task="whole feature", artifact=final_payload)
    assert "EVIDENCE / ARTIFACT:" in prompt
    assert "FR-001" in prompt
    assert "pytest" in prompt


# 13. teste de integração prova que artifact não se perde entre driver → AgentRunner
def test_13_integration_artifact_not_lost_between_driver_and_agent_runner(tmp_path):
    constitution_dir = tmp_path / ".specify" / "memory"
    constitution_dir.mkdir(parents=True, exist_ok=True)
    constitution_file = constitution_dir / "constitution.md"
    const_text = "# Integrated Constitution\nDeterministic Verification: Enforced."
    constitution_file.write_text(const_text, encoding="utf-8")

    provider = DummyProvider(name="codex", response_text='{"status": "PASS", "summary": "Valid constitution", "issues": []}')
    router = DummyRouter(provider="codex")
    runner = AgentRunner({"codex": provider}, router)

    gate_logs = []
    def on_call(role, prov, model, task_id, files, outputs, artifacts=()):
        gate_logs.append({
            "event": "AGENT_CALL",
            "role": role,
            "artifacts": list(artifacts),
        })
        return True

    runner.on_call = on_call

    def gate_callback(stage, role, files, commands, attempt=1, artifacts=()):
        gate_logs.append({
            "event": "STAGE_GATE",
            "stage": stage,
            "role": role,
            "artifacts": list(artifacts),
        })
        return True

    # Call _call_gate and run constitution validator as driver does
    const_rel = ".specify/memory/constitution.md"
    _call_gate(gate_callback, "CONSTITUTION_VALIDATE", "constitution_validator", [], [], artifacts=[const_rel], runner=runner)
    prompt = load_prompt("constitution_validator", feature="test-feature", artifact=constitution_file)
    result = runner.run("constitution_validator", prompt, cwd=tmp_path, allowed_paths=[], artifacts=[const_rel])

    assert result.success is True
    assert len(gate_logs) == 2
    # 1. Stage gate recorded artifacts
    assert gate_logs[0]["event"] == "STAGE_GATE"
    assert gate_logs[0]["stage"] == "CONSTITUTION_VALIDATE"
    assert gate_logs[0]["artifacts"] == [const_rel]

    # 2. AGENT_CALL received and retained the same artifacts
    assert gate_logs[1]["event"] == "AGENT_CALL"
    assert gate_logs[1]["role"] == "constitution_validator"
    assert gate_logs[1]["artifacts"] == [const_rel]

    # 3. Provider call prompt contains the exact constitution text
    assert len(provider.calls) == 1
    assert const_text in provider.calls[0]["prompt"]
    assert "CONSTITUTION TO VALIDATE:\n" + const_text in provider.calls[0]["prompt"]


# 14. nenhum validator tenta usar shell
def test_14_no_validator_attempts_shell():
    validator_roles = [
        "constitution_validator",
        "specification_validator",
        "plan_validator",
        "tasks_validator",
        "test_validator",
        "code_reviewer",
        "final_reviewer",
    ]

    for role in validator_roles:
        # Prompt contains explicit instruction
        prompt_text = load_prompt(role, feature="test", task="test", artifact="dummy content")
        assert "Do not attempt to read files via shell or execute any commands." in prompt_text
        assert "Analyze only the provided artifacts and context below." in prompt_text

        # Role metadata in ROLES has edits_files=False, validation=True
        role_def = ROLES[role]
        assert role_def.validation is True
        assert role_def.edits_files is False


# 15. Invariance assertion: stage declara artifacts_supplied não vazio -> AGENT_CALL não pode transformar em []
def test_15_invariance_assertion_raises_on_empty_agent_call_artifacts(tmp_path):
    gate = InteractiveGate(workspace=tmp_path, input_fn=lambda _: "continue")

    # 1. InteractiveGate invariance check
    gate.confirm("CONSTITUTION_VALIDATE", "constitution_validator", "codex", "gpt-6-luna", [], [], artifacts=[".specify/memory/constitution.md"])

    with pytest.raises(RuntimeError, match="INVARIANCE_VIOLATION.*declared artifacts_supplied.*received empty artifacts"):
        gate.confirm("AGENT_CALL", "constitution_validator", "codex", "gpt-6-luna", [], [], artifacts=[])

    # 2. AgentRunner invariance check
    provider = DummyProvider()
    runner = AgentRunner({"codex": provider}, DummyRouter())
    runner.set_stage_context("CONSTITUTION_VALIDATE", "constitution_validator", [".specify/memory/constitution.md"])

    with pytest.raises(RuntimeError, match="INVARIANCE_VIOLATION.*declared artifacts_supplied.*received empty artifacts"):
        runner.run("constitution_validator", "test prompt", cwd=tmp_path, artifacts=[])


# 16. Contract parameter aliases are properly unified to 'artifact'
def test_16_contract_aliases_unified_to_artifact():
    # 'artifact_content' alias
    p1 = load_prompt("constitution_validator", feature="test", artifact_content="# Principle 1")
    assert "# Principle 1" in p1

    # 'context' alias
    p2 = load_prompt("constitution_validator", feature="test", context="# Principle 2")
    assert "# Principle 2" in p2

    # 'artifacts' alias
    p3 = load_prompt("constitution_validator", feature="test", artifacts="# Principle 3")
    assert "# Principle 3" in p3

    # 'response' alias (when non-empty)
    p4 = load_prompt("constitution_validator", feature="test", response="# Principle 4")
    assert "# Principle 4" in p4
