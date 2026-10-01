from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRole:
    name: str
    tier: str
    prompt_file: str
    edits_files: bool = False
    validation: bool = False
    required_capabilities: tuple[str, ...] = ()
    preferred_capabilities: tuple[str, ...] = ()
    preferred_providers: tuple[str, ...] = ()
    prefer_different_provider_from_author: bool = False
    skill_name: str | None = None
    preferred_models: tuple[str, ...] = ()
    complex_preferred_models: tuple[str, ...] = ()
    deprecated: bool = False
    alias_for: str | None = None


ROLE_ALIASES: dict[str, str] = {
    "cross_artifact_validator": "consistency_agent",
    "clarifier": "clarifier_agent",
}


ROLES = {
    "constitution": AgentRole("constitution", "balanced", "constitution.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("codex", "agy", "opencode"), skill_name="speckit-constitution", preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    "constitution_validator": AgentRole("constitution_validator", "balanced", "constitution_validator.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), prefer_different_provider_from_author=True, preferred_models=("opencode-go/mimo-v2.6-pro", "gemini-3.8-flash-high", "gpt-6-sol")),
    "specification": AgentRole("specification", "balanced", "specification.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-specify", preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gpt-6-sol")),
    "specification_validator": AgentRole("specification_validator", "balanced", "specification_validator.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_capabilities=("STRUCTURED_OUTPUT",), preferred_providers=("agy", "opencode", "codex"), prefer_different_provider_from_author=True, preferred_models=("gemini-3.8-flash-high", "opencode-go/mimo-v2.6-pro", "gpt-6-sol")),
    "planning": AgentRole("planning", "balanced", "plan.md", True, required_capabilities=("REASONING", "DOCUMENT_GENERATION"), preferred_providers=("codex", "agy", "opencode"), skill_name="speckit-plan", preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    "plan_validator": AgentRole("plan_validator", "balanced", "plan_validator.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), prefer_different_provider_from_author=True, preferred_models=("opencode-go/mimo-v2.6-pro", "gemini-3.8-flash-high", "gpt-6-sol")),
    "tasks": AgentRole("tasks", "balanced", "tasks.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-tasks", preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gpt-6-sol"), complex_preferred_models=("opencode-go/mimo-v2.6-pro", "gpt-6-sol")),
    "tasks_validator": AgentRole("tasks_validator", "balanced", "tasks_validator.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("agy", "opencode", "codex"), prefer_different_provider_from_author=True, preferred_models=("gemini-3.8-flash-high", "opencode-go/mimo-v2.6-pro", "gpt-6-sol")),
    "cross_artifact_validator": AgentRole("cross_artifact_validator", "balanced", "cross_validator.md", True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("codex", "agy", "opencode"), prefer_different_provider_from_author=True, preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra"), skill_name="speckit-analyze", deprecated=True, alias_for="consistency_agent"),
    "test_designer": AgentRole("test_designer", "coding_strong", "test_designer.md", True, required_capabilities=("CODING", "FILE_EDITING"), preferred_capabilities=("AGENTIC_CODING",), preferred_providers=("opencode", "agy", "codex"), preferred_models=("opencode-go/mimo-v2.6-pro", "gemini-3.8-flash-high", "claude-sonnet-4-6", "gpt-6-sol")),
    "test_validator": AgentRole("test_validator", "balanced", "test_validator.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("agy", "opencode", "codex"), prefer_different_provider_from_author=True, preferred_models=("gemini-3.8-flash-high", "opencode-go/mimo-v2.6-pro", "gpt-6-sol")),
    "coder": AgentRole("coder", "coding_strong", "coder.md", True, required_capabilities=("CODING", "FILE_EDITING", "SHELL"), preferred_capabilities=("AGENTIC_CODING",), preferred_providers=("agy", "opencode", "codex"), skill_name="speckit-implement", preferred_models=("gemini-3.8-flash-high", "opencode-go/kimi-k3", "opencode-go/qwen3.8-flash", "gpt-6-sol"), complex_preferred_models=("gemini-3.8-flash-high", "gpt-6-sol", "gpt-6-astra")),
    "refactorer": AgentRole("refactorer", "coding_strong", "refactorer.md", True, required_capabilities=("CODING", "FILE_EDITING"), preferred_capabilities=("AGENTIC_CODING",), preferred_providers=("opencode", "agy", "codex"), preferred_models=("opencode-go/kimi-k3", "gemini-3.8-flash-high", "claude-sonnet-4-6", "gpt-6-sol"), complex_preferred_models=("opencode-go/kimi-k3", "gpt-6-sol", "gpt-6-astra")),
    "code_reviewer": AgentRole("code_reviewer", "balanced", "reviewer.md", validation=True, required_capabilities=("VALIDATION", "CODING"), preferred_providers=("opencode", "agy", "codex"), prefer_different_provider_from_author=True, preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gemini-3.8-flash-high", "gpt-6-sol")),
    "debugger": AgentRole("debugger", "balanced", "debugger.md", True, required_capabilities=("CODING", "REASONING"), preferred_providers=("opencode", "agy", "codex"), preferred_models=("opencode-go/mimo-v2.6-pro", "gemini-3.8-flash-high", "gpt-6-sol")),
    "final_reviewer": AgentRole("final_reviewer", "balanced", "reviewer.md", validation=True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("codex", "agy", "opencode"), prefer_different_provider_from_author=True, preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro", "gpt-6-astra"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    # Canonical SpecKit roles.
    "constitution_agent": AgentRole("constitution_agent", "balanced", "constitution.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("codex", "agy", "opencode"), skill_name="speckit-constitution", preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    "specification_agent": AgentRole("specification_agent", "balanced", "specification.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-specify", preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gpt-6-sol")),
    "clarifier": AgentRole("clarifier", "balanced", "specification.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("agy", "opencode", "codex"), skill_name="speckit-clarify", preferred_models=("gemini-3.8-flash-medium", "opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6"), alias_for="clarifier_agent"),
    "clarifier_agent": AgentRole("clarifier_agent", "balanced", "specification.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("agy", "opencode", "codex"), skill_name="speckit-clarify", preferred_models=("gemini-3.8-flash-medium", "opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6")),
    "requirements_reviewer": AgentRole("requirements_reviewer", "balanced", "specification_validator.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-checklist", preferred_models=("opencode-go/mimo-v2.6-pro", "gemini-3.8-flash-high", "gpt-6-sol")),
    "architect_agent": AgentRole("architect_agent", "balanced", "plan.md", True, required_capabilities=("REASONING", "DOCUMENT_GENERATION"), preferred_providers=("codex", "agy", "opencode"), skill_name="speckit-plan", preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    "task_agent": AgentRole("task_agent", "balanced", "tasks.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-tasks", preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gpt-6-sol"), complex_preferred_models=("opencode-go/mimo-v2.6-pro", "gpt-6-sol")),
    "consistency_agent": AgentRole("consistency_agent", "balanced", "cross_validator.md", True, required_capabilities=("VALIDATION", "REASONING"), preferred_providers=("codex", "agy", "opencode"), prefer_different_provider_from_author=True, skill_name="speckit-analyze", preferred_models=("gpt-6-sol", "claude-sonnet-4-6", "opencode-go/mimo-v2.6-pro"), complex_preferred_models=("gpt-6-sol", "gpt-6-astra")),
    "implementation_agent": AgentRole("implementation_agent", "coding_strong", "coder.md", True, required_capabilities=("CODING", "FILE_EDITING", "SHELL"), preferred_capabilities=("AGENTIC_CODING",), preferred_providers=("agy", "opencode", "codex"), skill_name="speckit-implement", preferred_models=("gemini-3.8-flash-high", "opencode-go/kimi-k3", "opencode-go/qwen3.8-flash", "gpt-6-sol"), complex_preferred_models=("gemini-3.8-flash-high", "gpt-6-sol", "gpt-6-astra")),
    "convergence_agent": AgentRole("convergence_agent", "balanced", "tasks.md", True, required_capabilities=("DOCUMENT_GENERATION", "REASONING"), preferred_providers=("opencode", "agy", "codex"), skill_name="speckit-converge", preferred_models=("opencode-go/mimo-v2.6-pro", "claude-sonnet-4-6", "gemini-3.8-flash-high", "gpt-6-sol"), complex_preferred_models=("opencode-go/mimo-v2.6-pro", "gpt-6-sol", "gpt-6-astra")),
}
