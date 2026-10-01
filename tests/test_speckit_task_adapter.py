"""SpecKit checklist compatibility and the TDD contract boundary."""

import json

import pytest

from orchestrator.workflow.driver import _create_and_validate
from orchestrator.workflow.task_adapter import TaskContractError, parse_speckit_tasks


def item(task_id="T001", dependencies=None, **changes):
    data = {
        "requirements": ["FR-001"],
        "acceptance_criteria": ["AC-001"],
        "plan_decisions": ["D-001"],
        "dependencies": dependencies or [],
        "test_type": "UNIT",
        "allowed_files": ["src/queue.py"],
        "tdd_phases": ["RED", "GREEN", "REFACTOR"],
    }
    data.update(changes)
    return (f"- [ ] {task_id} [US1] Implement queue in src/queue.py\n"
            f"  <!-- harness-task {json.dumps(data)} -->")


def test_reads_checklist_without_changing_the_speckit_artifact():
    source = "# Tasks: Queue\n\n## Phase 3: User Story 1\n" + item() + "\n" + item("T002", ["T001"])
    tasks = parse_speckit_tasks(source)
    assert [task["id"] for task in tasks] == ["T001", "T002"]
    assert tasks[1]["dependencies"] == ["T001"]
    assert tasks[0]["tdd_phases"] == ["RED", "GREEN", "REFACTOR"]
    assert source.startswith("# Tasks:")


@pytest.mark.parametrize("source,reason", [
    ("- [ ] T001 [US1] Implement in src/queue.py", "missing adjacent"),
    (item() + "\n" + item(), "Duplicate task"),
    (item("T002", ["T003"]), "earlier tasks"),
    (item(allowed_files=["../outside.py"]), "unsafe"),
    (item(allowed_files=["tests/test_queue.py"]), "unsafe"),
    (item(tdd_phases=["GREEN", "RED", "REFACTOR"]), "tdd_phases"),
    (item(requirements=[]), "requirement"),
    (item(test_type="NOT_AUTOMATABLE"), "non-automatable"),
    ("<!-- harness-task {} -->", "Orphan"),
    ("- [ ] T1 [US1] Implement in src/queue.py", "Malformed"),
])
def test_rejects_incomplete_or_unsafe_contracts(source, reason):
    with pytest.raises(TaskContractError, match=reason):
        parse_speckit_tasks(source)


def test_ignores_checklist_examples_in_fenced_code_and_keeps_legacy_resume():
    markdown = "```text\n- [ ] T099 example\n```\n" + item()
    assert [task["id"] for task in parse_speckit_tasks(markdown)] == ["T001"]
    assert parse_speckit_tasks('{"tasks":[{"id":"T001"}]}') == [{"id": "T001"}]


def test_checklist_id_remains_authoritative_and_bad_type_is_rejected():
    assert parse_speckit_tasks(item(id="T999"))[0]["id"] == "T001"
    with pytest.raises(TaskContractError, match="invalid test_type"):
        parse_speckit_tasks(item(test_type=[]))


def test_tasks_skill_file_is_preserved_when_stdout_is_completion_report(tmp_path):
    from orchestrator.config.models import AgentResult

    artifact = tmp_path / "tasks.md"

    class Runner:
        def run(self, role, prompt, **kwargs):
            if role == "tasks":
                artifact.write_text(item())
                return AgentResult("fake", None, role, True, stdout="Generated 1 task in tasks.md")
            return AgentResult("fake", None, role, True,
                               stdout='{"status":"PASS","summary":"ok","issues":[]}')

        def record_validation(self, result, validation):
            pass

    _create_and_validate(Runner(), "tasks", "tasks_validator", artifact, "feature", tmp_path, 10)
    assert artifact.read_text() == item()
