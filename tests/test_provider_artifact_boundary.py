"""Regression tests for the provider-result / canonical-filesystem boundary."""

import json
from pathlib import Path

import pytest

from orchestrator.config.models import AgentResult
from orchestrator.workflow.driver import WorkflowBlocked, _create_and_validate


AGY_ENVELOPE = json.dumps({
    "conversation_id": "fake-conversation",
    "status": "SUCCESS",
    "response": "",
    "duration_seconds": 1.0,
    "usage": {"input_tokens": 30, "output_tokens": 0},
})
TASKS = (
    "# Tasks\n\n- [ ] T001 Implement provider listing\n"
    '  <!-- harness-task {"requirements":["FR-001"],"acceptance_criteria":["AC-001"],'
    '"plan_decisions":["D-001"],"dependencies":[],"test_type":"UNIT",'
    '"allowed_files":["src/providers.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->\n'
)
ARTIFACT_CASES = [
    ("specification", "specification_validator", "spec.md",
     "# Feature Specification\n\nFR-001: List providers. AC-001: Output includes configured names.\n"),
    ("planning", "plan_validator", "plan.md",
     "# Technical Plan\n\nD-001: Use the existing provider registry to list configured names.\n"),
    ("tasks", "tasks_validator", "tasks.md", TASKS),
]


class FileWritingRunner:
    def __init__(self, artifact: Path, content: str | None, stdout: str):
        self.artifact = artifact
        self.content = content
        self.stdout = stdout
        self.calls: list[tuple[str, str, dict]] = []

    def run(self, role, prompt, **kwargs):
        self.calls.append((role, prompt, kwargs))
        if role in {"specification", "planning", "tasks"}:
            if self.content is not None:
                self.artifact.parent.mkdir(parents=True, exist_ok=True)
                self.artifact.write_text(self.content)
            return AgentResult("agy", "gemini-3.8-flash-medium", role, True, stdout=self.stdout)
        assert kwargs["allowed_paths"] == []
        assert kwargs["exclude_providers"] == {"agy"}
        return AgentResult("codex", "gpt-6-astra", role, True,
                           stdout='{"status":"PASS","summary":"Filesystem content validated","issues":[]}')


@pytest.mark.parametrize("author,validator,filename,content", ARTIFACT_CASES)
@pytest.mark.parametrize("stdout", [AGY_ENVELOPE, "", "Unrelated diagnostics: completed 1 tool call."])
def test_valid_skill_file_survives_provider_envelope_empty_or_arbitrary_stdout(
    tmp_path, author, validator, filename, content, stdout,
):
    artifact = tmp_path / filename
    runner = FileWritingRunner(artifact, content, stdout)
    _, verdict = _create_and_validate(
        runner, author, validator, artifact, "Feature request", tmp_path, 10,
        allowed_paths=[filename],
    )
    assert verdict.status == "PASS"
    assert artifact.read_text() == content
    reviewer_role, reviewer_prompt, reviewer_options = runner.calls[-1]
    assert reviewer_role == validator
    assert content in reviewer_prompt
    assert "fake-conversation" not in reviewer_prompt
    assert reviewer_options["artifacts"] == [filename]
    assert runner.calls[0][2]["allowed_paths"] == [filename]


@pytest.mark.parametrize("author,validator,filename,content", ARTIFACT_CASES)
@pytest.mark.parametrize("initial", [None, "valid", "template"])
@pytest.mark.parametrize("stdout", [AGY_ENVELOPE, "", "provider printed arbitrary text"])
def test_success_without_filesystem_change_blocks_without_promoting_stdout(
    tmp_path, author, validator, filename, content, initial, stdout,
):
    artifact = tmp_path / filename
    before = None if initial is None else content if initial == "valid" else "# [FEATURE NAME]\n\n[TODO]\n"
    if before is not None:
        artifact.write_text(before)
    runner = FileWritingRunner(artifact, None, stdout)
    with pytest.raises(WorkflowBlocked):
        _create_and_validate(
            runner, author, validator, artifact, "Feature request", tmp_path, 10,
            allowed_paths=[filename],
        )
    assert [call[0] for call in runner.calls] == [author]
    assert artifact.read_text() == before if before is not None else not artifact.exists()


@pytest.mark.parametrize("author,validator,filename,content", ARTIFACT_CASES)
@pytest.mark.parametrize("invalid_content", [AGY_ENVELOPE, "# [FEATURE NAME]\n\n[TODO]\n"])
def test_invalid_skill_file_blocks_before_semantic_validator(
    tmp_path, author, validator, filename, content, invalid_content,
):
    artifact = tmp_path / filename
    runner = FileWritingRunner(artifact, invalid_content, content)
    with pytest.raises(WorkflowBlocked):
        _create_and_validate(
            runner, author, validator, artifact, "Feature request", tmp_path, 10,
            allowed_paths=[filename],
        )
    assert [call[0] for call in runner.calls] == [author]
    assert artifact.read_text() == invalid_content
