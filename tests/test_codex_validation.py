import json
from types import SimpleNamespace

from orchestrator.config.models import AgentResult
from orchestrator.storage.sqlite import StateStore
from orchestrator.agents.runner import AgentRunner
from orchestrator.validation.parser import (
    parse_codex_validation,
    parse_validation,
)


def _setup_store_and_workflow(tmp_path, feature="test-codex"):
    db_path = tmp_path / ".orchestrator" / "state" / "orchestrator.sqlite3"
    store = StateStore(db_path)
    wid = store.create_workflow(
        feature,
        {
            "stage": "CONSTITUTION_VALIDATE",
            "workspace": str(tmp_path),
            "first_real_run": False,
            "attempts": {},
            "completed_tasks": [],
            "blocked_tasks": [],
            "validation_results": [],
            "verification_results": [],
            "state_schema_version": store.SCHEMA_VERSION,
        },
    )
    return store, wid


class DummyRouter:
    def route(self, role, **kwargs):
        return SimpleNamespace(provider="codex", model="gpt-6-luna")


def _make_codex_jsonl(
    final_agent_text: str | None = None,
    intermediate_prose: str = "Let me inspect the workspace and run preliminary checks.",
    include_agent_message: bool = True,
    truncated_line: str | None = None,
) -> str:
    events = [
        {"type": "thread.started", "thread_id": "th_123"},
        {"type": "turn.started", "turn_id": "turn_001"},
    ]
    if intermediate_prose:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": intermediate_prose,
                },
            }
        )
    events.extend(
        [
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "command": "git status --porcelain",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "git status --porcelain",
                    "exit_code": 0,
                    "stdout": "",
                },
            },
        ]
    )
    if include_agent_message and final_agent_text is not None:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": final_agent_text,
                },
            }
        )
    events.append({"type": "turn.completed", "turn_id": "turn_001"})

    lines = [json.dumps(ev) for ev in events]
    if truncated_line is not None:
        lines.append(truncated_line)
    return "\n".join(lines)


def test_codex_jsonl_selects_last_agent_message_and_ignores_intermediate_prose_and_commands():
    final_contract = json.dumps(
        {
            "status": "PASS",
            "issues": [],
            "summary": "All checks passed successfully",
        }
    )
    jsonl = _make_codex_jsonl(
        final_agent_text=final_contract,
        intermediate_prose="Intermediate prose that is NOT json contract and should be ignored.",
    )

    result = parse_codex_validation(jsonl, "constitution_validator", "gpt-6-luna")
    assert result.status == "PASS"
    assert result.summary == "All checks passed successfully"
    assert result.issues == []
    assert result.raw_output == jsonl

    # Also test via parse_validation with and without provider="codex"
    res_direct = parse_validation(jsonl, "constitution_validator", "gpt-6-luna")
    assert res_direct.status == "PASS"
    assert res_direct.summary == "All checks passed successfully"

    res_provider = parse_validation(jsonl, "constitution_validator", "gpt-6-luna", provider="codex")
    assert res_provider.status == "PASS"
    assert res_provider.summary == "All checks passed successfully"


def test_codex_jsonl_pass():
    final_contract = json.dumps(
        {
            "status": "PASS",
            "issues": [],
            "summary": "Specification fully conforms to standards",
        }
    )
    jsonl = _make_codex_jsonl(final_agent_text=final_contract)

    result = parse_codex_validation(jsonl, "specification_validator", "gpt-6-luna")
    assert result.status == "PASS"
    assert result.summary == "Specification fully conforms to standards"
    assert result.issues == []
    assert result.raw_output == jsonl


def test_codex_jsonl_revise():
    final_contract = json.dumps(
        {
            "status": "REVISE",
            "issues": [
                "Ambiguous error response format in spec.md line 42 (FR-002)"
            ],
            "summary": "Clarifications needed in section 2",
        }
    )
    jsonl = _make_codex_jsonl(final_agent_text=final_contract)

    result = parse_codex_validation(jsonl, "specification_validator", "gpt-6-luna")
    assert result.status == "REVISE"
    assert result.summary == "Clarifications needed in section 2"
    assert len(result.issues) == 1
    assert result.issues[0] == "Ambiguous error response format in spec.md line 42 (FR-002)"


def test_codex_jsonl_blocked():
    final_contract = json.dumps(
        {
            "status": "BLOCKED",
            "issues": [
                "Rule violation directly conflicts with constitution (SUPREMACY)"
            ],
            "summary": "Fatal constitutional flaw detected",
        }
    )
    jsonl = _make_codex_jsonl(final_agent_text=final_contract)

    result = parse_codex_validation(jsonl, "constitution_validator", "gpt-6-luna")
    assert result.status == "BLOCKED"
    assert result.status != "PARSE_ERROR"
    assert result.summary == "Fatal constitutional flaw detected"
    assert len(result.issues) == 1
    assert result.issues[0] == "Rule violation directly conflicts with constitution (SUPREMACY)"
    assert result.raw_output == jsonl


def test_codex_jsonl_blocked_persists_as_blocked_not_parse_error(tmp_path):
    store, wid = _setup_store_and_workflow(tmp_path)
    runner = AgentRunner({}, DummyRouter(), store=store, workflow_id=wid)

    final_contract = json.dumps(
        {
            "status": "BLOCKED",
            "issues": [
                "Unauthorized architecture bypass: defect B-99 in plan.md"
            ],
            "summary": "Workflow blocked due to critical architectural defect",
        }
    )
    jsonl = _make_codex_jsonl(
        final_agent_text=final_contract,
        intermediate_prose="Analyzing plan architecture: secret=sk-SECRET12345678901234567890",
    )

    parsed = parse_validation(jsonl, "plan_validator", "gpt-6-luna", provider="codex")
    assert parsed.status == "BLOCKED"
    assert parsed.status != "PARSE_ERROR"

    agent_res = AgentResult("codex", "gpt-6-luna", "plan_validator", True, stdout=jsonl)
    runner.record_validation(agent_res, parsed, stage="PLAN_VALIDATE")

    wf = store.get_workflow(wid)
    results = wf["state"]["validation_results"]
    assert len(results) == 1
    assert results[0]["status"] == "BLOCKED"
    assert results[0]["status"] != "PARSE_ERROR"
    assert results[0]["reason"] == "Workflow blocked due to critical architectural defect"
    assert len(results[0]["issues"]) == 1
    assert results[0]["issues"][0] == "Unauthorized architecture bypass: defect B-99 in plan.md"
    # Preserves sanitized raw_response
    assert "sk-SECRET12345678901234567890" not in results[0]["raw_response"]
    assert "[REDACTED]" in results[0]["raw_response"]
    assert "thread.started" in results[0]["raw_response"]
    assert "turn.completed" in results[0]["raw_response"]



def test_codex_jsonl_agent_message_final_invalido():
    # Final agent message contains plain prose instead of JSON contract
    invalid_final_text = "I reviewed everything and it looks pretty solid to me. Approved!"
    jsonl = _make_codex_jsonl(final_agent_text=invalid_final_text)

    result = parse_codex_validation(jsonl, "tasks_validator", "gpt-6-luna")
    assert result.status == "PARSE_ERROR"
    assert result.summary == "Could not parse strict validation output"
    assert result.raw_output == jsonl

    res_direct = parse_validation(jsonl, "tasks_validator", "gpt-6-luna", provider="codex")
    assert res_direct.status == "PARSE_ERROR"


def test_codex_jsonl_agent_message_final_malformed_json():
    # Final agent message has bad JSON or missing required fields
    malformed_json = '{"status": "UNKNOWN_STATUS", "issues": []}'
    jsonl = _make_codex_jsonl(final_agent_text=malformed_json)

    result = parse_codex_validation(jsonl, "tasks_validator", "gpt-6-luna")
    assert result.status == "PARSE_ERROR"
    assert result.summary == "Could not parse strict validation output"

    malformed_issues = '{"status": "PASS", "issues": "not-a-list"}'
    jsonl2 = _make_codex_jsonl(final_agent_text=malformed_issues)
    result2 = parse_codex_validation(jsonl2, "tasks_validator", "gpt-6-luna")
    assert result2.status == "PARSE_ERROR"


def test_codex_jsonl_nenhum_agent_message():
    # Stream with commands and thread events, but NO agent_message
    jsonl = _make_codex_jsonl(include_agent_message=False, intermediate_prose="")

    result = parse_codex_validation(jsonl, "test_validator", "gpt-6-luna")
    assert result.status == "PARSE_ERROR"
    assert result.summary == "Could not parse strict validation output"
    assert result.raw_output == jsonl

    res_direct = parse_validation(jsonl, "test_validator", "gpt-6-luna", provider="codex")
    assert res_direct.status == "PARSE_ERROR"


def test_codex_jsonl_truncado():
    # 1. Truncated mid-line on the final agent message
    truncated_1 = (
        '{"type":"thread.started"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"status\\":\\"'
    )
    res1 = parse_codex_validation(truncated_1, "validator", "model")
    assert res1.status == "PARSE_ERROR"
    assert res1.summary == "Could not parse strict validation output"

    # 2. Truncated object structure
    truncated_2 = '{"type":"item.completed","item":{"type":"agent_message"'
    res2 = parse_codex_validation(truncated_2, "validator", "model")
    assert res2.status == "PARSE_ERROR"

    # 3. Truncated with trailing incomplete JSONL
    truncated_3 = _make_codex_jsonl(
        final_agent_text=json.dumps({"status": "PASS", "issues": [], "summary": "ok"}),
        truncated_line='{"type":"item.started","it',
    )
    res3 = parse_codex_validation(truncated_3, "validator", "model")
    assert res3.status == "PARSE_ERROR"

    # 4. Empty string
    res4 = parse_codex_validation("", "validator", "model")
    assert res4.status == "PARSE_ERROR"

    # 5. Only whitespace
    res5 = parse_codex_validation("   \n\n  \t  ", "validator", "model")
    assert res5.status == "PARSE_ERROR"


def test_codex_jsonl_multiple_intermediate_prose_messages():
    # Multiple intermediate agent messages, ensuring only the last one is parsed
    events = [
        {"type": "thread.started"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Thought 1: inspect files."}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "git diff"}},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Thought 2: analyze diff."}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "pytest"}},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0}},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps({"status": "PASS", "issues": [], "summary": "Final decision PASS"}),
            },
        },
        {"type": "turn.completed"},
    ]
    jsonl = "\n".join(json.dumps(ev) for ev in events)

    result = parse_codex_validation(jsonl, "code_reviewer", "gpt-6-luna")
    assert result.status == "PASS"
    assert result.summary == "Final decision PASS"


def test_codex_jsonl_fenced_json_in_final_agent_message():
    fenced_text = 'Validation report:\n```json\n{"status":"PASS","issues":[],"summary":"Fenced passed"}\n```'
    jsonl = _make_codex_jsonl(final_agent_text=fenced_text)

    result = parse_codex_validation(jsonl, "code_reviewer", "gpt-6-luna")
    assert result.status == "PASS"
    assert result.summary == "Fenced passed"
