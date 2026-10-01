"""Declarative SpecKit stage metadata for the Python control plane.

This module describes which installed skill serves each stage. It deliberately
does not contain skill instructions or implement their methodology.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


Mutability = Literal["read_only", "artifact_write", "workspace_write"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retryable_errors: tuple[str, ...] = ("PROVIDER_ERROR", "TIMEOUT")


@dataclass(frozen=True)
class Stage:
    name: str
    skill_name: str
    role: str
    optional: bool = False
    once_per_project: bool = False
    expected_artifacts: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    mutability: Mutability = "artifact_write"
    retry_policy: RetryPolicy = RetryPolicy()


_STAGES = (
    Stage(
        name="CONSTITUTION",
        skill_name="speckit-constitution",
        role="constitution_agent",
        once_per_project=True,
        expected_artifacts=(".specify/memory/constitution.md",),
    ),
    Stage(
        name="SPECIFICATION",
        skill_name="speckit-specify",
        role="specification_agent",
        expected_artifacts=("specs/{feature}/spec.md",),
        prerequisites=("CONSTITUTION",),
    ),
    Stage(
        name="CLARIFICATION",
        skill_name="speckit-clarify",
        role="clarifier_agent",
        optional=True,
        expected_artifacts=("specs/{feature}/spec.md",),
        prerequisites=("SPECIFICATION",),
    ),
    Stage(
        name="REQUIREMENTS_CHECKLIST",
        skill_name="speckit-checklist",
        role="requirements_reviewer",
        optional=True,
        expected_artifacts=("specs/{feature}/checklists/",),
        prerequisites=("SPECIFICATION",),
    ),
    Stage(
        name="PLAN",
        skill_name="speckit-plan",
        role="architect_agent",
        expected_artifacts=("specs/{feature}/plan.md",),
        prerequisites=("SPECIFICATION",),
    ),
    Stage(
        name="TASKS",
        skill_name="speckit-tasks",
        role="task_agent",
        expected_artifacts=("specs/{feature}/tasks.md",),
        prerequisites=("PLAN",),
    ),
    Stage(
        name="ANALYSIS",
        skill_name="speckit-analyze",
        role="consistency_agent",
        # The analysis report is operational output, not a source artifact.
        expected_artifacts=(),
        prerequisites=("TASKS",),
        mutability="read_only",
    ),
    Stage(
        name="IMPLEMENTATION",
        skill_name="speckit-implement",
        role="implementation_agent",
        expected_artifacts=("workspace files authorized by task scope",),
        prerequisites=("ANALYSIS",),
        mutability="workspace_write",
    ),
    Stage(
        name="CONVERGENCE",
        skill_name="speckit-converge",
        role="convergence_agent",
        expected_artifacts=("specs/{feature}/tasks.md",),
        prerequisites=("IMPLEMENTATION",),
    ),
)


class StageRegistry:
    """Read-only lookup of the workflow's declared stages."""

    def __init__(self, stages: tuple[Stage, ...] = _STAGES) -> None:
        by_name = {stage.name: stage for stage in stages}
        if len(by_name) != len(stages):
            raise ValueError("Stage names must be unique")
        for stage in stages:
            missing = set(stage.prerequisites) - by_name.keys()
            if missing:
                raise ValueError(
                    f"Stage {stage.name!r} has unknown prerequisites: {sorted(missing)}"
                )
        self._stages = stages
        self._by_name: Mapping[str, Stage] = MappingProxyType(by_name)

    def get(self, name: str) -> Stage:
        return self._by_name[name]

    def all(self) -> tuple[Stage, ...]:
        return self._stages

    def for_role(self, role: str) -> tuple[Stage, ...]:
        return tuple(stage for stage in self._stages if stage.role == role)


STAGE_REGISTRY = StageRegistry()
