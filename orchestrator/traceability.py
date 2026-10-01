"""Persistent requirement-to-verification evidence."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceabilityRecord:
    requirement_id: str
    acceptance_criteria_ids: list[str] = field(default_factory=list)
    plan_decisions: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)
    production_files: list[str] = field(default_factory=list)
    verification_results: list[dict[str,Any]] = field(default_factory=list)
    final_status: str = "PENDING"


def records_from_task(task_data: dict, evidence: dict) -> list[TraceabilityRecord]:
    return [TraceabilityRecord(
        requirement_id=requirement,
        acceptance_criteria_ids=list(task_data.get("acceptance_criteria",[])),
        plan_decisions=list(task_data.get("plan_decisions",[])),
        task_ids=[str(task_data["id"])],
        test_ids=list(evidence.get("test_design",{}).get("created_tests",[])),
        production_files=list(evidence.get("production_files_changed",[])),
        verification_results=list(evidence.get("verification_results",[])),
        final_status="PASS" if evidence.get("final_status")=="COMPLETE" else "BLOCKED",
    ) for requirement in task_data.get("requirements",[])]
