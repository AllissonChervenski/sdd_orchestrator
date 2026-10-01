import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from orchestrator.config.models import AgentResult, Config
from orchestrator.storage.sqlite import StateStore
from orchestrator.workflow.driver import WorkflowBlocked, run_sdd_workflow
from orchestrator.workflow.constitution import (
    CRITICAL_CONSTITUTION_PLACEHOLDERS,
    classify_constitution,
    find_constitution_placeholders,
    is_uninitialized_constitution,
)
from orchestrator.workflow.resume import WorkspaceFingerprint


REAL_CONSTITUTION_TEXT = """# Agents EMG Constitution

## Core Principles

### I. Deterministic Orchestration
The Python orchestrator deterministically governs all workflows, checkpoints, and gates.

### II. Test-First Rigor (NON-NEGOTIABLE)
All implementation follows strict RED-GREEN-REFACTOR cycles without test tampering.

## Governance
Constitution supersedes all other documentation.

**Version**: 1.0.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-09-28
"""

OFFICIAL_TEMPLATE_SAMPLE = """# [PROJECT_NAME] Constitution

## Core Principles

### [PRINCIPLE_1_NAME]
[PRINCIPLE_1_DESCRIPTION]

### [PRINCIPLE_2_NAME]
[PRINCIPLE_2_DESCRIPTION]

## Governance
[GOVERNANCE_RULES]

**Version**: [CONSTITUTION_VERSION] | **Ratified**: [RATIFICATION_DATE] | **Last Amended**: [LAST_AMENDED_DATE]
"""


def _setup_workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    store_path = root / ".orchestrator" / "state" / "orchestrator.sqlite3"
    store = StateStore(store_path)
    wid = store.create_workflow(
        "test-feature",
        {
            "stage": "CONSTITUTION",
            "workspace": str(root),
            "first_real_run": False,
            "attempts": {},
            "completed_tasks": [],
            "blocked_tasks": [],
            "validation_results": [],
            "verification_results": [],
            "state_schema_version": store.SCHEMA_VERSION,
        },
    )
    workflow_dir = root / ".orchestrator" / "runs" / wid
    workflow_dir.mkdir(parents=True)
    return root, store, wid, workflow_dir


class MockAgentRunner:
    def __init__(self, generated_constitution=REAL_CONSTITUTION_TEXT):
        self.calls: list[dict] = []
        self.generated_constitution = generated_constitution
        self.validation_results: list[dict] = []

    def run(self, role, prompt, *args, **kwargs):
        self.calls.append({"method": "run", "role": role, "prompt": prompt})
        if role == "constitution_validator":
            return AgentResult(
                "codex",
                "gpt-6-luna",
                role,
                True,
                stdout=json.dumps({"status": "PASS", "issues": [], "summary": "Constitution valid"}),
            )
        if role == "constitution":
            const_path = Path(kwargs["cwd"]) / ".specify" / "memory" / "constitution.md"
            const_path.parent.mkdir(parents=True, exist_ok=True)
            const_path.write_text(self.generated_constitution)
            return AgentResult(
                "agy",
                "gemini-3.8-flash-medium",
                role,
                True,
                stdout=json.dumps({"conversation_id": "fake-bootstrap", "status": "SUCCESS", "response": ""}),
            )
        if role == "specification":
            raise StopWorkflow("Reached specification")
        raise AssertionError(f"Unexpected run call: {role}")

    def run_skill(self, role, provider, model, skill_name, prompt, cwd, *args, **kwargs):
        self.calls.append({"method": "run_skill", "role": role, "skill_name": skill_name, "prompt": prompt})
        if role == "constitution":
            # Native skill writes the file to .specify/memory/constitution.md
            const_path = Path(cwd) / ".specify" / "memory" / "constitution.md"
            const_path.parent.mkdir(parents=True, exist_ok=True)
            const_path.write_text(self.generated_constitution)
            return AgentResult(
                "agy",
                "gemini-3.8-flash-medium",
                role,
                True,
                stdout=f"installed skill {skill_name} completed successfully",
            )
        if role == "specification":
            raise StopWorkflow("Reached specification")
        raise AssertionError(f"Unexpected run_skill call: {role}")

    def record_validation(self, agent_res, val_res, stage=None, evidence=None):
        self.validation_results.append({
            "role": agent_res.role,
            "status": val_res.status,
            "summary": val_res.summary,
            "stage": stage,
        })


class StopWorkflow(Exception):
    pass


# -------------------------------------------------------------------------
# Unit Tests for Deterministic Constitution Classification
# -------------------------------------------------------------------------

def test_classify_constitution_absent(tmp_path):
    missing = tmp_path / "nonexistent" / "constitution.md"
    assert classify_constitution(missing) == "ABSENT"
    assert is_uninitialized_constitution(missing) is True


def test_classify_constitution_empty_file(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n  \t  ")
    assert classify_constitution(empty) == "UNINITIALIZED_CONSTITUTION"
    assert is_uninitialized_constitution(empty) is True


def test_classify_constitution_official_template():
    assert classify_constitution(OFFICIAL_TEMPLATE_SAMPLE) == "UNINITIALIZED_CONSTITUTION"
    assert is_uninitialized_constitution(OFFICIAL_TEMPLATE_SAMPLE) is True
    found = find_constitution_placeholders(OFFICIAL_TEMPLATE_SAMPLE)
    for p in CRITICAL_CONSTITUTION_PLACEHOLDERS:
        assert p in found


def test_classify_constitution_partially_filled():
    partially_filled = (
        "# Custom App Constitution\n\n"
        "### [PRINCIPLE_1_NAME]\n"
        "[PRINCIPLE_1_DESCRIPTION]\n\n"
        "**Version**: [CONSTITUTION_VERSION]\n"
    )
    assert classify_constitution(partially_filled) == "UNINITIALIZED_CONSTITUTION"
    assert is_uninitialized_constitution(partially_filled) is True
    found = find_constitution_placeholders(partially_filled)
    assert "[PRINCIPLE_1_NAME]" in found
    assert "[CONSTITUTION_VERSION]" in found


def test_classify_constitution_real():
    assert classify_constitution(REAL_CONSTITUTION_TEXT) == "VALID"
    assert is_uninitialized_constitution(REAL_CONSTITUTION_TEXT) is False
    assert find_constitution_placeholders(REAL_CONSTITUTION_TEXT) == []


def test_classify_constitution_ignores_regular_markdown_links():
    text_with_links = (
        "# Valid Project Constitution\n\n"
        "See [GUIDE](https://example.com/guide) and [DOCS](file:///path/to/docs.md) for details.\n\n"
        "Version: 1.0.0 | Ratified: 2026-09-28\n"
    )
    assert classify_constitution(text_with_links) == "VALID"
    assert is_uninitialized_constitution(text_with_links) is False


# -------------------------------------------------------------------------
# Integration Tests for run_sdd_workflow Lifecycle
# -------------------------------------------------------------------------

def test_constitution_ausente_triggers_bootstrap(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    assert not const_file.exists()

    runner = MockAgentRunner()
    approved = []

    def approve():
        approved.append(True)
        return True

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            approve_constitution=approve,
            store=store,
        )

    # Approve was requested
    assert approved == [True]
    # Constitution generator (speckit-constitution) was called
    called_roles = [c["role"] for c in runner.calls]
    assert "constitution" in called_roles
    assert "constitution_validator" in called_roles
    # Constitution now exists and is valid on disk
    assert const_file.is_file()
    assert classify_constitution(const_file) == "VALID"
    # Checkpoints created
    cps = store.checkpoints(wid)
    stages = [cp["stage"] for cp in cps]
    assert "CONSTITUTION_CREATED" in stages
    assert "CONSTITUTION_VALIDATED" in stages


def test_constitution_template_oficial_triggers_bootstrap(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(OFFICIAL_TEMPLATE_SAMPLE)
    assert classify_constitution(const_file) == "UNINITIALIZED_CONSTITUTION"

    runner = MockAgentRunner()
    approved = []

    def approve():
        approved.append(True)
        return True

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            approve_constitution=approve,
            store=store,
        )

    # Approved called
    assert approved == [True]
    # Bootstrapped via constitution role, replacing template with real constitution
    called_roles = [c["role"] for c in runner.calls]
    assert "constitution" in called_roles
    assert "constitution_validator" in called_roles
    assert classify_constitution(const_file) == "VALID"


def test_constitution_parcialmente_preenchido_bloqueio_sem_aprovacao(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(
        "# Partially Filled\n\n### [PRINCIPLE_1_NAME]\n[PRINCIPLE_1_DESCRIPTION]\n"
    )
    assert classify_constitution(const_file) == "UNINITIALIZED_CONSTITUTION"

    runner = MockAgentRunner()

    # Reject human approval
    def deny():
        return False

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            approve_constitution=deny,
            store=store,
        )

    assert "Constitution generation requires human approval" in str(exc_info.value)
    # No agent was called
    assert runner.calls == []


def test_constitution_parcialmente_preenchido_bloqueio_em_resume(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(
        "# Partial\n\n**Version**: [CONSTITUTION_VERSION]\n"
    )

    # Simulate checkpoint already exists in store for CONSTITUTION_VALIDATED
    fp = WorkspaceFingerprint(root).capture(wid)
    store.create_checkpoint(wid, "CONSTITUTION_VALIDATED:-:1", "CONSTITUTION_VALIDATED", fp)

    runner = MockAgentRunner()

    with pytest.raises(WorkflowBlocked) as exc_info:
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            store=store,
            resume=True,
        )

    assert "postcondition failure" in str(exc_info.value)


def test_constitution_real_nao_regenera(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(REAL_CONSTITUTION_TEXT)
    assert classify_constitution(const_file) == "VALID"

    runner = MockAgentRunner()
    approved = []

    def approve():
        approved.append(True)
        return True

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            approve_constitution=approve,
            store=store,
        )

    # Approve was NOT requested because constitution is already real
    assert approved == []
    # Generation was NOT called
    called_roles = [c["role"] for c in runner.calls]
    assert "constitution" not in called_roles
    # Validation ONLY was called
    assert "constitution_validator" in called_roles


def test_constitution_validada_resume_skips(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(REAL_CONSTITUTION_TEXT)

    fp = WorkspaceFingerprint(root).capture(wid)
    store.create_checkpoint(wid, "CONSTITUTION_VALIDATED:-:1", "CONSTITUTION_VALIDATED", fp)

    runner = MockAgentRunner()

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            store=store,
            resume=True,
        )

    # In resume mode with CONSTITUTION_VALIDATED, neither generation nor validation was called
    called_roles = [c["role"] for c in runner.calls]
    assert "constitution" not in called_roles
    assert "constitution_validator" not in called_roles
    assert "specification" in called_roles


def test_template_nunca_chega_diretamente_a_constitution_validator_como_se_fosse_valida(tmp_path):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    const_file = root / ".specify" / "memory" / "constitution.md"
    const_file.parent.mkdir(parents=True, exist_ok=True)
    const_file.write_text(OFFICIAL_TEMPLATE_SAMPLE)

    runner = MockAgentRunner(generated_constitution=REAL_CONSTITUTION_TEXT)

    def approve():
        return True

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "test-feature",
            root,
            runner,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir,
            approve_constitution=approve,
            store=store,
        )

    # Verify call sequence: bootstrap (constitution) MUST precede validation (constitution_validator)
    called_roles = [c["role"] for c in runner.calls]
    assert "constitution" in called_roles
    assert "constitution_validator" in called_roles
    assert called_roles.index("constitution") < called_roles.index("constitution_validator")

    # Verify that the file on disk was already replaced with real constitution
    content_on_disk = const_file.read_text()
    for placeholder in CRITICAL_CONSTITUTION_PLACEHOLDERS:
        assert placeholder not in content_on_disk, f"Template placeholder {placeholder} remained on disk"
    assert "Agents EMG Constitution" in content_on_disk

    # Furthermore, if human approval is not granted, constitution_validator is NEVER called
    root2, store2, wid2, workflow_dir2 = _setup_workspace(tmp_path / "denied")
    const_file2 = root2 / ".specify" / "memory" / "constitution.md"
    const_file2.parent.mkdir(parents=True, exist_ok=True)
    const_file2.write_text(OFFICIAL_TEMPLATE_SAMPLE)
    runner2 = MockAgentRunner()

    with pytest.raises(WorkflowBlocked):
        run_sdd_workflow(
            "test-feature",
            root2,
            runner2,
            SimpleNamespace(requirement_results=[]),
            Config(),
            workflow_dir2,
            approve_constitution=lambda: False,
            store=store2,
        )

    # In denied approval, constitution_validator was NEVER invoked
    assert "constitution_validator" not in [c["role"] for c in runner2.calls]
    assert runner2.calls == []


@pytest.mark.parametrize("stdout", [
    json.dumps({"conversation_id": "fake-constitution", "status": "SUCCESS", "response": "",
                "duration_seconds": 1.0, "usage": {"input_tokens": 12, "output_tokens": 0}}),
    "", "Provider completed its command",
])
@pytest.mark.parametrize("initial", [None, OFFICIAL_TEMPLATE_SAMPLE])
@pytest.mark.parametrize("creates_file", [True, False])
def test_constitution_success_requires_skill_filesystem_output(tmp_path, stdout, initial, creates_file):
    root, store, wid, workflow_dir = _setup_workspace(tmp_path)
    artifact = root / ".specify/memory/constitution.md"
    if initial is not None:
        artifact.parent.mkdir(parents=True)
        artifact.write_text(initial)

    class EnvelopeRunner(MockAgentRunner):
        def run_skill(self, role, provider, model, skill_name, prompt, cwd, **kwargs):
            if role != "constitution":
                return super().run_skill(role, provider, model, skill_name, prompt, cwd, **kwargs)
            assert skill_name == "speckit-constitution"
            assert kwargs["allowed_paths"] == [".specify/memory/constitution.md"]
            if creates_file:
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(REAL_CONSTITUTION_TEXT)
            return AgentResult("agy", "gemini-3.8-flash-medium", role, True, stdout=stdout)

        def run(self, role, prompt, *args, **kwargs):
            if role == "constitution_validator":
                assert kwargs["allowed_paths"] == []
                assert REAL_CONSTITUTION_TEXT in prompt
                assert "fake-constitution" not in prompt
            return super().run(role, prompt, *args, **kwargs)

    runner = EnvelopeRunner()
    with pytest.raises(StopWorkflow if creates_file else WorkflowBlocked):
        run_sdd_workflow(
            "test-feature", root, runner, SimpleNamespace(requirement_results=[]), Config(),
            workflow_dir, approve_constitution=lambda: True, store=store,
        )
    if creates_file:
        assert artifact.read_text() == REAL_CONSTITUTION_TEXT
        assert "CONSTITUTION_VALIDATED" in [cp["stage"] for cp in store.checkpoints(wid)]
    else:
        assert artifact.read_text() == initial if initial is not None else not artifact.exists()
        assert "constitution_validator" not in [call["role"] for call in runner.calls]
        assert store.checkpoints(wid) == []
