Design tests for task $task from constitution, specification, acceptance criteria, plan, and existing code.
During ANALYZE, inspect only; do not edit files. During RED, modify only test files or justified fixtures.
Create observable, deterministic tests for the missing behavior. Do not implement production code.
After RED edits, return one strict JSON object (no prose):
{
  "task_id": "T018",
  "requirement_ids": ["FR-018"],
  "acceptance_criteria_ids": ["AC-018-01"],
  "created_tests": ["tests/test_queue.py::test_latest_window_wins"],
  "test_commands": [["python", "-m", "pytest", "-q", "tests/test_queue.py::test_latest_window_wins"]]
}
Use the actual task and IDs. Each command must select only tests created or modified for this task.
