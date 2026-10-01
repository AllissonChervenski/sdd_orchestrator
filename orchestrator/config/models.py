from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ModelCapabilities:
    provider: str
    model_id: str
    display_name: str | None = None
    available: bool = True
    context_window: int | None = None
    supports_coding: bool | None = None
    supports_reasoning: bool | None = None
    supports_structured_output: bool | None = None
    supports_file_editing: bool | None = None
    supports_shell: bool | None = None
    supports_agentic_work: bool | None = None
    speed_class: str | None = None
    cost_class: str | None = None
    metadata_source: str | None = None
    confidence: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderCapabilities:
    provider: str
    cli_available: bool = False
    cli_version: str | None = None
    models: list[str] = field(default_factory=list)
    supports_headless: bool = False
    supports_json: bool = False
    supports_sessions: bool = False
    supports_model_selection: bool = False
    supports_agent_selection: bool = False
    supports_file_editing: bool = False
    supports_shell: bool = False
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    model_details: list[ModelCapabilities] = field(default_factory=list)
    supports_ponytail: bool | None = None
    supports_caveman: bool | None = None


@dataclass
class AgentResult:
    provider: str
    model: str | None
    role: str
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    structured_output: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    error: str | None = None
    resolved_model: str | None = None
    resolution_source: str = "unavailable"
    requested_effort: str | None = None
    resolved_effort: str | None = None
    fallback_reason: str | None = None


@dataclass
class ValidationIssue:
    id: str
    severity: str
    artifact: str
    location: str
    requirement: str
    description: str
    suggested_action: str


@dataclass
class ValidationResult:
    status: str
    issues: list[str]
    summary: str
    validator: str
    model: str | None
    timestamp: str
    raw_output: str = ""

    @property
    def reason(self) -> str:
        return self.summary


@dataclass
class Config:
    providers: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, Any] = field(default_factory=dict)
    retries: dict[str, int] = field(default_factory=lambda: {"artifact_generation": 3, "implementation": 3, "review": 2})
    verification: dict[str, Any] = field(default_factory=lambda: {"task_tests": [], "regression_tests": [], "build": [], "tests": [], "lint": [], "static": [], "requirements": []})
    models: dict[str, Any] = field(default_factory=dict)
    human_gates: dict[str, Any] = field(default_factory=lambda: {"clarification_fallback": "block"})
    timeouts: dict[str, int] = field(default_factory=lambda: {"provider": 600, "verification": 600})
    routing: dict[str, Any] = field(default_factory=lambda: {"adaptive_routing_mode":"observe","historical_min_samples":10,"decay_half_life_days":30,"exploration_rate":0.05})
    real_run: dict[str, Any] = field(default_factory=lambda: {"safety_mode":"strict","max_agent_calls_per_task":12,"max_agent_calls_per_workflow":100,"max_retries":3,"max_wall_time":7200,"max_convergence_iterations":3})
    cost_optimization: dict[str, Any] = field(default_factory=lambda: {"mode":"observe","profile":"balanced","ladder":[]})
    execution_policies: dict[str, Any] = field(default_factory=lambda: {"prompt_fallback":False,"ponytail_roles":["coder","refactorer"]})
    path: Path = Path("orchestrator.yaml")
