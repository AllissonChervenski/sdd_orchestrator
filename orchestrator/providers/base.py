from abc import ABC, abstractmethod
from pathlib import Path
from orchestrator.config.models import AgentResult, ProviderCapabilities
from orchestrator.agents.skills import ResolvedSkill


class AgentProvider(ABC):
    name = "base"

    @abstractmethod
    def discover(self) -> ProviderCapabilities: ...

    @abstractmethod
    def build_command(self, prompt: str, role: str, model: str | None = None, cwd: str | Path | None = None, permissions: str | None = None) -> list[str]: ...

    @abstractmethod
    def run(self, prompt: str, role: str, model: str | None = None, cwd: str | Path | None = None, timeout: int | None = None, permissions: str | None = None) -> AgentResult: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    def run_skill(self, skill: ResolvedSkill, arguments: str, role: str,
                  model: str | None = None, cwd: str | Path | None = None,
                  timeout: int | None = None, permissions: str | None = None,
                  policy_prefix: str = "") -> AgentResult:
        """Fallback for adapters without a native skill integration."""
        prompt = self.inline_skill_prompt(skill, arguments, policy_prefix)
        return self.run(prompt, role, model, cwd, timeout, permissions)

    @staticmethod
    def inline_skill_prompt(skill: ResolvedSkill, arguments: str, policy_prefix: str = "") -> str:
        content = skill.path.read_text(encoding="utf-8")
        parts = [policy_prefix, f"Execute the installed skill {skill.name} from {skill.path}.",
                 f"<installed-skill>\n{content}\n</installed-skill>",
                 f"Arguments:\n{arguments}"]
        return "\n\n".join(part for part in parts if part)
