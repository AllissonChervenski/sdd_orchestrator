from pathlib import Path

import pytest

from orchestrator.agents.runner import AgentRunner
from orchestrator.agents.router import ModelRouter
from orchestrator.agents.skills import SkillDispatcher, SkillResolutionError, SkillResolver
from orchestrator.config.models import AgentResult, ProviderCapabilities
from orchestrator.providers.agy import AgyProvider
from orchestrator.providers.codex import CodexProvider
from orchestrator.providers.opencode import OpenCodeProvider


def install(root: Path, name: str = "speckit-plan") -> Path:
    path = root / ".agents" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("Installed instructions: $ARGUMENTS", encoding="utf-8")
    return path


def test_resolver_uses_installed_source_and_provider_specific_native_path(tmp_path):
    source = install(tmp_path)
    command = tmp_path / ".opencode" / "commands" / "speckit.plan.md"
    command.parent.mkdir(parents=True)
    command.write_text("command", encoding="utf-8")
    resolver = SkillResolver()
    for provider in ("agy", "codex"):
        resolved = resolver.resolve("speckit-plan", provider, tmp_path, ("REASONING",))
        assert (resolved.path, resolved.delivery, resolved.required_capabilities) == (
            source, "native", ("REASONING",)
        )
    open_code = resolver.resolve("speckit-plan", "opencode", tmp_path)
    assert (open_code.path, open_code.delivery, open_code.command) == (
        source, "native", "speckit.plan"
    )
    command.unlink()
    assert resolver.resolve("speckit-plan", "opencode", tmp_path).delivery == "inline"


def test_resolver_rejects_missing_or_escaping_skill(tmp_path):
    resolver = SkillResolver()
    with pytest.raises(SkillResolutionError, match="Invalid skill name"):
        resolver.resolve("../secret", "codex", tmp_path)
    with pytest.raises(SkillResolutionError, match="SKILL.md not found"):
        resolver.resolve("speckit-plan", "codex", tmp_path)
    outside = tmp_path.parent / "outside-skill.md"
    outside.write_text("untrusted", encoding="utf-8")
    path = tmp_path / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    with pytest.raises(SkillResolutionError, match="SKILL.md not found"):
        resolver.resolve("speckit-plan", "codex", tmp_path)


def test_resolver_falls_back_to_installed_skill_for_codex_without_native_directory(tmp_path):
    source = tmp_path / ".claude" / "skills" / "speckit-plan" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("installed fallback", encoding="utf-8")
    resolved = SkillResolver().resolve("speckit-plan", "codex", tmp_path)
    assert (resolved.path, resolved.delivery) == (source, "inline")


@pytest.mark.parametrize("provider_type,native_prefix", [
    (AgyProvider, "/skill:speckit-plan"),
    (CodexProvider, "$speckit-plan"),
])
def test_native_skill_invocation_uses_provider_syntax(tmp_path, monkeypatch, provider_type, native_prefix):
    install(tmp_path)
    commands = []
    monkeypatch.setattr(
        f"orchestrator.providers.{provider_type.name}.execute",
        lambda *args: commands.append(args[3]) or AgentResult(provider_type.name, "m", "architect_agent", True),
    )
    dispatcher = SkillDispatcher({provider_type.name: provider_type()})
    result = dispatcher.run_skill("architect_agent", provider_type.name, "m", "speckit-plan",
                                  "feature X", tmp_path, timeout=12)
    assert result.success
    assert any(item.startswith(native_prefix) and "feature X" in item for item in commands[0])
    assert "Installed instructions" not in str(commands[0])


def test_opencode_uses_installed_command_and_inline_fallback(tmp_path, monkeypatch):
    source = install(tmp_path)
    command = tmp_path / ".opencode" / "commands" / "speckit.plan.md"
    command.parent.mkdir(parents=True)
    command.write_text("command", encoding="utf-8")
    commands = []
    monkeypatch.setattr("orchestrator.providers.opencode.execute",
                        lambda *args: commands.append(args[3]) or AgentResult("opencode", "m", "architect_agent", True))
    provider = OpenCodeProvider()
    provider._run_help = "--model --format --dir"
    dispatcher = SkillDispatcher({"opencode": provider})
    assert dispatcher.run_skill("architect_agent", "opencode", "m", "speckit-plan", "feature X", tmp_path).success
    assert commands[0][2:4] == ["--command", "speckit.plan"]
    assert commands[0][-1] == "feature X"
    command.unlink()
    assert dispatcher.run_skill("architect_agent", "opencode", "m", "speckit-plan", "feature Y", tmp_path).success
    assert "--command" not in commands[1]
    assert source.read_text() in commands[1][-1]
    assert "feature Y" in commands[1][-1]


def test_runner_skill_dispatch_keeps_scope_and_timeout(tmp_path):
    install(tmp_path)

    class Provider:
        def __init__(self):
            self.calls = []

        def run_skill(self, skill, arguments, role, model, cwd, timeout, permissions, prefix):
            self.calls.append((skill, arguments, timeout, permissions, prefix))
            (cwd / "outside.py").write_text("changed")
            return AgentResult("codex", model, role, True)

    provider = Provider()
    cap = ProviderCapabilities("codex", cli_available=True, models=["m"],
                               supports_model_selection=True, supports_file_editing=True,
                               supports_shell=True)
    router = ModelRouter({"codex": cap})
    runner = AgentRunner({"codex": provider}, router, safety={"safety_mode": "strict"})
    result = runner.run_skill("architect_agent", "codex", "m", "speckit-plan", "feature",
                              tmp_path, timeout=7, allowed_paths=["specs/"])
    assert result.error == "SCOPE_VIOLATION: outside.py"
    assert provider.calls[0][0].required_capabilities == ("REASONING", "DOCUMENT_GENERATION")
    assert provider.calls[0][2:4] == (7, None)
    assert result.usage["scope_violations"] == ["outside.py"]


def test_runner_skill_dispatch_preserves_partial_write_block(tmp_path):
    install(tmp_path)

    class Provider:
        def run_skill(self, skill, arguments, role, model, cwd, timeout, permissions, prefix):
            path = cwd / "specs" / "feature" / "plan.md"
            path.parent.mkdir(parents=True)
            path.write_text("partial", encoding="utf-8")
            return AgentResult("codex", model, role, False, error="PROVIDER_FAILURE")

    cap = ProviderCapabilities("codex", cli_available=True, models=["m"],
                               supports_model_selection=True, supports_file_editing=True,
                               supports_shell=True)
    runner = AgentRunner({"codex": Provider()}, ModelRouter({"codex": cap}),
                         safety={"safety_mode": "strict"})
    result = runner.run_skill("architect_agent", "codex", "m", "speckit-plan", "feature",
                              tmp_path, allowed_paths=["specs/"])
    assert result.error == "PARTIAL_WRITE: provider failed after modifying specs/feature/plan.md"
    assert result.usage["partial_files"] == ["specs/feature/plan.md"]


def test_missing_skill_never_calls_provider(tmp_path):
    class Provider:
        def run_skill(self, *args):
            raise AssertionError("provider must not run")

    result = SkillDispatcher({"codex": Provider()}).run_skill(
        "architect_agent", "codex", "m", "speckit-plan", "feature", tmp_path
    )
    assert result.error.startswith("SKILL_NOT_FOUND:")
