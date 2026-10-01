from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from orchestrator.agents.roles import ROLES
from orchestrator.workflow.stages import STAGE_REGISTRY, Stage, StageRegistry


def test_every_declared_stage_resolves_to_its_role_and_installed_skill():
    for stage in STAGE_REGISTRY.all():
        role = ROLES[stage.role]
        assert role.skill_name == stage.skill_name
        assert any(
            (path / "SKILL.md").is_file()
            for path in (
                Path(".agents/skills") / stage.skill_name,
                Path(".claude/skills") / stage.skill_name,
            )
        ) or (Path(".opencode/commands") / f"{stage.skill_name.replace('-', '.')}.md").is_file()


def test_constitution_is_once_per_project_and_stages_keep_declared_dependencies():
    constitution = STAGE_REGISTRY.get("CONSTITUTION")
    assert constitution.once_per_project is True
    assert constitution.prerequisites == ()
    assert STAGE_REGISTRY.get("TASKS").prerequisites == ("PLAN",)
    assert STAGE_REGISTRY.get("ANALYSIS").mutability == "read_only"
    assert STAGE_REGISTRY.get("ANALYSIS").expected_artifacts == ()
    assert STAGE_REGISTRY.get("IMPLEMENTATION").mutability == "workspace_write"


def test_registry_is_declarative_and_does_not_route_or_run_providers():
    assert STAGE_REGISTRY.get("SPECIFICATION").skill_name == "speckit-specify"
    assert STAGE_REGISTRY.for_role("specification_agent") == (
        STAGE_REGISTRY.get("SPECIFICATION"),
    )
    assert all(stage.skill_name != "speckit-auto" for stage in STAGE_REGISTRY.all())
    with pytest.raises(FrozenInstanceError):
        STAGE_REGISTRY.get("SPECIFICATION").role = "agy"  # type: ignore[misc]


def test_registry_rejects_duplicate_names_and_unknown_prerequisites():
    stage = Stage("ONE", "speckit-specify", "specification_agent")
    with pytest.raises(ValueError, match="unique"):
        StageRegistry((stage, stage))
    with pytest.raises(ValueError, match="unknown prerequisites"):
        StageRegistry((Stage("TWO", "speckit-plan", "architect_agent", prerequisites=("MISSING",)),))


def test_spec_kit_roles_remain_provider_router_workers():
    worker_providers = {"agy", "codex", "opencode"}
    for stage in STAGE_REGISTRY.all():
        role = ROLES[stage.role]
        assert set(role.preferred_providers) == worker_providers
