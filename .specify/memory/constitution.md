<!--
Sync Impact Report:
- Version change: 1.0.0 → 1.1.0
- Rationale: Added Principle VI (Scientific and Numerical Oracle Integrity) to formalize provenance and immutability requirements for scientific, numerical, and DSP fixtures ahead of feature development.
- Impact on existing features: Fully backward-compatible. Feature 001-provider-summary is governed by Principles III and IV (local inspection, dual-format output); Principle VI establishes forward invariants for numerical/DSP pipelines without retroactively altering provider-summary behavior.
- Nexus to active feature: Feature 001-provider-summary serves as the architectural canary validating that the orchestrator control plane, model resolution, family separation, and protected gates function before numerical workloads execute under Principle VI.
- List of modified principles:
  - Added: VI. Scientific and Numerical Oracle Integrity
- Added sections: None
- Removed sections: None
- Follow-up TODOs: None
-->

# SDD Orchestrator Constitution

## Core Principles

### I. Python Central Orchestrator & Deterministic Gatekeeper (NON-NEGOTIABLE)
Python (`orchestrator/`) is the sole orchestrator and state authority for the system. External agent providers (Antigravity, Codex, OpenCode, Claude Code) are untrusted workers invoked strictly to produce or inspect artifacts. Python owns all state machine transitions (SDD and TDD), persistence in SQLite, workspace fingerprinting, scope containment (`SCOPE_VIOLATION`), and deterministic verification. No LLM response, agent output, or external tool can override or bypass a failing deterministic gate (test failure, lint issue, typing error, or syntax compilation failure).

### II. Strict TDD Lifecycle & Anti-Tampering Protection (NON-NEGOTIABLE)
All code changes MUST follow the strict RED → GREEN → REFACTOR lifecycle per task contract (`T###`).
- **RED**: The test designer must produce dedicated tests in `tests/` demonstrating an expected failure (`EXPECTED_FAILURE`). Python validates that the test fails for the expected missing feature before implementation begins.
- **GREEN**: The implementer modifies only the files specified in `allowed_files` to make the task tests pass. Test files created in RED are protected by SHA-256 snapshots; any alteration, weakening of assertions, or skipping of tests during GREEN or REFACTOR is blocked as `TEST_TAMPERING`.
- **REFACTOR**: Code restructuring must maintain green tests across both the task suite and full regression.

### III. Non-Destructive Local Reuse & Zero-Subprocess Read Paths
Commands and features designed for inspection, reporting, or summary (such as `provider-summary` or catalog queries) MUST be strictly read-only and non-destructive.
- They MUST NOT invoke external agent CLIs, dispatch LLM model calls, execute subprocesses, or run live smoke tests.
- They MUST reuse existing local abstractions, registries, and configuration caches (e.g., `capabilities.json`, `models.json`, and capability registries) rather than duplicating discovery or routing logic.
- They MUST NOT introduce unnecessary architectural layers or external dependencies when existing project abstractions suffice.

### IV. CLI Ergonomics, Text/JSON Duality & Failure Containment
New or modified user-facing CLI commands (such as `provider-summary`) MUST provide predictable, robust interfaces:
- Dual-format output: Commands designed for dual-format reporting MUST provide a standard human-readable text output and a pure machine-readable JSON output via `--json`. When `--json` is supplied, stdout MUST contain exclusively parseable JSON without extraneous logs or banners. Existing commands outside feature scope (such as `doctor`) MUST preserve their established behavior and MUST NOT be modified unless explicitly required by a dedicated feature.
- Error handling: Expected operational errors MUST exit with a non-zero exit code, print clear diagnostics to stderr, and suppress unhandled stack traces during normal operation.

### V. SpecKit Traceability & Living Documentation Supremacy
Living documentation organized under `specs/<feature>/` (`spec.md`, `plan.md`, `tasks.md`, `checklists/`) is the authoritative source of truth for features.
- All functional requirements (`FR-xxx`) and acceptance criteria (`AC-xxx`) must maintain end-to-end traceability to tasks and tests.
- This Constitution in `.specify/memory/constitution.md` supersedes all downstream specifications, plans, and task breakdowns. If a conflict arises, the Constitution prevails.

### VI. Scientific and Numerical Oracle Integrity
Scientific and numerical reference artifacts used as test oracles MUST have explicit provenance and MUST NOT be generated from the implementation under test.

Protected golden/reference artifacts and registered tolerance contracts MUST remain immutable during implementation phases unless changed through an explicitly authorized requirements/test-design revision.

## Technical Constraints & Safe Execution Policies
- **Runtime Environment**: Python 3.10+ standard library baseline. Optional development dependencies (`pytest`, `PyYAML`, `ruff`, `mypy`) are managed via `pyproject.toml`.
- **Command Safety Policy**: Execution of destructive commands (`rm -rf`, `git reset --hard`, `git push --force`) is strictly forbidden and blocked by Python command policies.
- **Scope Invariants**: Core components—including `ModelRouter`, cost policies, `StageRegistry`, `SkillDispatcher`, and the `TDD` engine—MUST NOT be modified unless explicitly required by an authorized architectural initiative. Features MUST NOT introduce new third-party dependencies without prior governance approval.

## Quality Gates & Multi-Agent Verification Matrix
- **Deterministic Pipeline**: Every change MUST pass four deterministic quality gates:
  1. Unit and regression tests: `python -m pytest -q`
  2. Linting and formatting: `python -m ruff check orchestrator tests`
  3. Static type analysis: `python -m mypy`
  4. Syntax validation: `python -m compileall -q orchestrator tests`
- **Independent Validation**: Artifacts (specs, plans, tasks, test designs, diffs) MUST be reviewed by an independent validator role preferably routed to a distinct agent provider from the authoring agent (`prefer_different_provider_from_author`).
- **Checkpoint Persistence**: Phase transitions and state snapshots MUST be durably recorded in SQLite alongside workspace fingerprints prior to advancing.

## Governance
- **Supremacy**: This Constitution defines the non-negotiable operational principles of the repository. All agents, automations, and human contributors MUST comply.
- **Amendment Procedure**: Any amendment requires an explicit proposal detailing the rationale, impact on existing features, and an accompanying update to `.specify/memory/constitution.md`.
- **Versioning Policy**: Semantic versioning (MAJOR.MINOR.PATCH) governs this document:
  - MAJOR: Incompatible governance changes, removal or redefinition of core principles.
  - MINOR: Addition of new principles or material expansions to governance sections.
  - PATCH: Clarifications, wording refinements, non-semantic typographical updates.
- **Compliance Review**: Every feature pipeline validation and code review must explicitly verify adherence to these principles before convergence and final sign-off.

**Version**: 1.1.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-09-29
