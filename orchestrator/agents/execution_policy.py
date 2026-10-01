"""Prompt-only Ponytail/Caveman fallback, independent from model routing."""
from dataclasses import dataclass


PONYTAIL_STANDARD=(
    "Coding policy Ponytail: make the smallest correct diff; reuse standard library and existing primitives; "
    "avoid speculative abstractions, dependencies, and unrelated code. Preserve every requirement, acceptance criterion, "
    "test, necessary error handler, security control, and accessibility behavior."
)
PONYTAIL_COMPACT=(
    "Ponytail: smallest correct diff; reuse existing code. Preserve requirements, tests, errors, security, accessibility."
)
CAVEMAN_STANDARD=(
    "Communication policy Caveman: use concise prose. Preserve mandatory structured output and JSON schemas, "
    "code, commands, paths, errors, requirement and test IDs, and verification evidence exactly."
)
CAVEMAN_COMPACT=(
    "Caveman: be concise. Preserve JSON, code, commands, paths, IDs, errors, and evidence."
)


@dataclass(frozen=True)
class ExecutionPolicy:
    ponytail_enabled: bool
    ponytail_mode: str
    caveman_enabled: bool
    caveman_mode: str
    policy_source: str
    policy_overhead_estimate: int
    policy_enabled_reason: str
    prefix: str


class ExecutionPolicyRouter:
    def __init__(self, config=None, capabilities=None):
        self.config=config or {}
        self.capabilities=capabilities or {}

    def select(self, role, provider, profile):
        cap=self.capabilities.get(provider)
        native_ponytail=bool(getattr(cap,"supports_ponytail",False))
        native_caveman=bool(getattr(cap,"supports_caveman",False))
        prompt_fallback=bool(self.config.get("prompt_fallback",False))
        ponytail_roles=set(self.config.get("ponytail_roles",["coder","refactorer"]))
        ponytail_requested=role in ponytail_roles
        caveman_requested=role not in set(self.config.get("caveman_excluded_roles",[]))
        # Native skill support is recorded only when discovery actually reports it.
        ponytail=ponytail_requested and (native_ponytail or prompt_fallback)
        caveman=caveman_requested and (native_caveman or prompt_fallback)
        trivial=profile.complexity=="LOW" and profile.scope=="LOCAL"
        ponytail_mode="compact" if ponytail and trivial else ("standard" if ponytail else "off")
        caveman_mode="compact" if caveman and trivial else ("standard" if caveman else "off")
        parts=[]
        if ponytail and not native_ponytail: parts.append(PONYTAIL_COMPACT if trivial else PONYTAIL_STANDARD)
        if caveman and not native_caveman: parts.append(CAVEMAN_COMPACT if trivial else CAVEMAN_STANDARD)
        source="skill" if (native_ponytail and ponytail) or (native_caveman and caveman) else ("prompt" if parts else "unavailable")
        prefix="\n".join(parts)
        reason=("compact policy minimizes prompt overhead for local low-complexity work" if trivial and parts
                else "role policy enabled; mandatory evidence and tests preserved" if ponytail or caveman
                else "skill unavailable and prompt fallback disabled")
        return ExecutionPolicy(ponytail,ponytail_mode,caveman,caveman_mode,source,(len(prefix)+3)//4,reason,prefix)
