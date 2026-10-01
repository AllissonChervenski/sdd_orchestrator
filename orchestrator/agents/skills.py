"""Resolve installed SpecKit skills without copying their methodology into Python."""

from dataclasses import dataclass
from pathlib import Path
import re

from orchestrator.config.models import AgentResult
from orchestrator.agents.roles import ROLES


_SKILL_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True)
class ResolvedSkill:
    name: str
    path: Path
    delivery: str
    command: str | None = None
    required_capabilities: tuple[str, ...] = ()


class SkillResolutionError(ValueError):
    pass


class SkillResolver:
    """Inspect the active workspace's installed provider integrations."""

    _directories = {"agy": ".agents/skills", "codex": ".agents/skills"}

    def resolve(self, skill_name: str, provider: str, cwd: str | Path,
                required_capabilities: tuple[str, ...] = ()) -> ResolvedSkill:
        if not _SKILL_NAME.fullmatch(skill_name):
            raise SkillResolutionError(f"Invalid skill name: {skill_name!r}")
        root = Path(cwd).resolve()
        sources = (".agents/skills", ".claude/skills")
        source = next((candidate for directory in sources
                       if (candidate := root / directory / skill_name / "SKILL.md").is_file()
                       and candidate.resolve().is_relative_to(root)), None)
        if source is None:
            raise SkillResolutionError(f"Installed SKILL.md not found for {skill_name!r} in {root}")
        if provider == "opencode":
            command = skill_name.replace("-", ".", 1)
            command_path = root / ".opencode" / "commands" / f"{command}.md"
            if command_path.is_file() and command_path.resolve().is_relative_to(root):
                return ResolvedSkill(skill_name, source, "native", command, required_capabilities)
        if provider in self._directories:
            path = root / self._directories[provider] / skill_name / "SKILL.md"
            if path.is_file() and path.resolve().is_relative_to(root):
                return ResolvedSkill(skill_name, path, "native", required_capabilities=required_capabilities)
        return ResolvedSkill(skill_name, source, "inline", required_capabilities=required_capabilities)


class SkillDispatcher:
    """Pass a resolved skill to a provider adapter; the adapter owns CLI syntax."""

    def __init__(self, providers, resolver: SkillResolver | None = None):
        self.providers = providers
        self.resolver = resolver or SkillResolver()

    def run_skill(self, role, provider, model, skill_name, arguments, cwd,
                  execution_policy=None, timeout=None, permissions=None) -> AgentResult:
        if provider not in self.providers:
            return AgentResult(provider, model, role, False, error="PROVIDER_FAILURE: provider adapter unavailable")
        try:
            skill = self.resolver.resolve(skill_name, provider, cwd,
                                          ROLES[role].required_capabilities)
        except SkillResolutionError as exc:
            return AgentResult(provider, model, role, False, error=f"SKILL_NOT_FOUND: {exc}")
        prefix = getattr(execution_policy, "prefix", "") if execution_policy else ""
        return self.providers[provider].run_skill(
            skill, arguments, role, model, cwd, timeout, permissions, prefix
        )
