---
description: "SpecKit task checklist with harness metadata"
---

# Tasks: provider-summary

**Input**: Design documents from `/specs/001-provider-summary/`
**Prerequisites**: `spec.md` and `plan.md` (both available and reviewed).

Keep the SpecKit checklist format exactly: `- [ ] T001 [P?] [US1?] Description with file path`.
Each task has adjacent harness metadata. Implementation `allowed_files` lists only exact production paths; the RED TestDesigner declares the task-specific test files and commands before implementation.

## TDD Contract

- TDD applies to all four implementation tasks: each runs RED → GREEN → REFACTOR under the Python orchestrator.
- In RED, the TestDesigner creates task-specific tests in `tests/`, declares the focused test command, and demonstrates `EXPECTED_FAILURE` caused by the missing behavior. The orchestrator snapshots those tests; GREEN and REFACTOR must not edit or weaken them.
- GREEN is restricted to the production `allowed_files` listed for that task. REFACTOR preserves task and regression tests.
- TDD exceptions: T005 is a non-implementation, manual diff/contract verification task. It has `NOT_AUTOMATABLE`, with justification and alternative verification in its metadata; it does not waive the TDD lifecycle for implementation.

## Phase 1: Setup

**Purpose**: Shared project preparation. No setup changes are needed: the existing CLI, provider registry, capability cache, and pytest harness are reused.

## Phase 2: Foundational

**Purpose**: Blocking prerequisites. No shared production foundation is needed; each story reuses established CLI/cache abstractions. Implementation tasks are serialized because they all modify `orchestrator/cli.py`.

## Phase 3: User Story 1 — Resumo Textual dos Providers Conhecidos (P1)

**Goal**: Show every registered provider in text with confirmed status and a stable integer model count.

**Independent Test**: Run `python -m orchestrator provider-summary`; verify exit code 0 and each registry entry appears with `OK` only for `cli_available is True`, otherwise `UNAVAILABLE`, and `N discovered` (including zero for an absent/unusable catalog). The RED TestDesigner will supply fixtures and the focused test command.

- [ ] T001 [US1] Implement the read-only text provider summary using the existing registry and cached capabilities in `orchestrator/cli.py`.
  <!-- harness-task {"requirements":["FR-001","FR-003","FR-004","FR-006","FR-007"],"acceptance_criteria":["AC-001","AC-003","AC-004","AC-005","AC-007","AC-008","AC-010","AC-011"],"plan_decisions":["D-001","D-002","D-003","D-004"],"dependencies":[],"test_type":"INTEGRATION","allowed_files":["orchestrator/cli.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->

**Checkpoint**: Story 1 is independently complete when its focused RED tests pass after GREEN/REFACTOR and the output reflects the registry/cache without discovery.

## Phase 4: User Story 2 — Saída Estruturada em JSON (P1)

**Goal**: Provide the canonical typed JSON report with stdout reserved for the JSON payload.

**Independent Test**: Run `python -m orchestrator provider-summary --json`; parse all stdout and verify exactly the top-level `providers` key and item keys `name`, `available`, and `model_count`, with `str`, strict `bool`, and `int` values. Verify missing availability/catalog values are `false`/`0`.

- [ ] T002 [US2] Add canonical `--json` serialization from the same provider summary data in `orchestrator/cli.py`.
  <!-- harness-task {"requirements":["FR-002","FR-003","FR-004","FR-006","FR-007"],"acceptance_criteria":["AC-002","AC-003","AC-004","AC-005","AC-007","AC-008","AC-010","AC-011"],"plan_decisions":["D-001","D-002","D-003","D-004"],"dependencies":["T001"],"test_type":"CONTRACT","allowed_files":["orchestrator/cli.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->

**Checkpoint**: Story 2 is independently complete when the JSON contract and stdout-purity tests pass, including strict field types and stable defaults.

## Phase 5: User Story 3 — Operação Estritamente Somente Leitura e Local (P1)

**Goal**: Prove both output modes reuse local abstractions without discovery, probes, subprocesses, API calls, or smoke tests.

**Independent Test**: With discovery/probe/subprocess boundaries patched to fail if called, run the text and JSON command paths using deterministic local cache data; both complete successfully without invoking those boundaries.

- [ ] T003 [US3] Enforce registry/cache-only data access for both provider summary formats in `orchestrator/cli.py`.
  <!-- harness-task {"requirements":["FR-003","FR-004","FR-006","FR-007"],"acceptance_criteria":["AC-003","AC-004","AC-005","AC-007","AC-008"],"plan_decisions":["D-002","D-003","D-004"],"dependencies":["T002"],"test_type":"INTEGRATION","allowed_files":["orchestrator/cli.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->

**Checkpoint**: Story 3 is independently complete when both modes pass no-discovery/no-subprocess tests and derive provider names from the registry.

## Phase 6: User Story 4 — Tratamento Controlado de Falhas Esperadas (P2)

**Goal**: Reject unsupported flags and contain expected local cache read/shape failures with non-zero status and concise stderr diagnostics.

**Independent Test**: Invoke the command with an unsupported flag and inject expected local read/shape failures. Verify non-zero status, useful stderr, and no raw traceback; argparse retains its standard usage handling.

- [ ] T004 [US4] Handle expected provider-summary cache errors at the CLI command boundary in `orchestrator/cli.py`.
  <!-- harness-task {"requirements":["FR-005","FR-006","FR-007"],"acceptance_criteria":["AC-007","AC-008","AC-009"],"plan_decisions":["D-001","D-005"],"dependencies":["T003"],"test_type":"INTEGRATION","allowed_files":["orchestrator/cli.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->

**Checkpoint**: Story 4 is independently complete when invalid arguments and expected local data failures return non-zero without unhandled tracebacks.

## Final Phase: Polish & Cross-Cutting Concerns

- [ ] T005 Review the final feature diff against protected-component, dependency, read-only, and regression requirements in `specs/001-provider-summary/tasks.md`.
  <!-- harness-task {"requirements":["FR-003","FR-006","FR-007"],"acceptance_criteria":["AC-004","AC-005","AC-006","AC-007","AC-008"],"plan_decisions":["D-002","D-005"],"dependencies":["T004"],"test_type":"NOT_AUTOMATABLE","justification":"This is a human review of the final diff and scope invariants; the harness cannot determine whether protected architecture or behavior was changed beyond path checks.","alternative_verification":"Review the final diff and verify protected components, doctor behavior, provider registry/adapters, dependency manifests, and unrelated files are unchanged. At the orchestrated verification gate, run `python -m pytest -q`, `python -m ruff check orchestrator tests`, `python -m mypy`, and `python -m compileall -q orchestrator tests`; record each result in orchestrator evidence.","allowed_files":[],"tdd_phases":[]} -->

## Dependencies & Execution Order

- Setup and foundational phases require no changes.
- Story order: US1 → US2 → US3 → US4. Tasks T001–T004 are sequential because each changes the same `orchestrator/cli.py`; each task's own tests are authored first in RED and protected thereafter.
- T005 follows all implementation tasks and is a manual review plus the final deterministic quality gate.

```text
T001 → T002 → T003 → T004 → T005
```

## Parallel Examples

No implementation tasks can safely run in parallel because they share `orchestrator/cli.py` and each task depends on the prior CLI increment. During each task's RED phase, the TestDesigner may author that task's focused tests before GREEN begins; the Python orchestrator controls the phase transition.

## Implementation Strategy

Deliver the text summary as the MVP (US1), then add JSON serialization (US2), establish the local-only invariant for both modes (US3), and add expected-error containment (US4). Complete the final review and project quality gates after all four TDD cycles. Preserve the test designer's RED test files unchanged during GREEN and REFACTOR.

## Phase 7: Convergence

- [ ] T006 Produce auditable Python-orchestrated RED → GREEN → REFACTOR evidence for implementation tasks T001–T004, including expected RED failures and protected test snapshots, per FR-006 / AC-007 (missing).
  <!-- harness-task {"requirements":["FR-006"],"acceptance_criteria":["AC-007"],"plan_decisions":[],"dependencies":["T005"],"test_type":"NOT_AUTOMATABLE","justification":"The existing green test suite proves current behavior but cannot establish prior expected RED failures, orchestrator transitions, or immutable test snapshots; no persisted orchestration evidence is present.","alternative_verification":"Run or recover the formal Python orchestrator evidence for T001–T004. Confirm each task recorded EXPECTED_FAILURE in RED, the orchestrator captured test hashes, GREEN and REFACTOR passed without test tampering, and phase checkpoints are persisted. If evidence cannot be recovered, repeat the tasks through the authorized orchestrator TDD lifecycle.","allowed_files":[],"tdd_phases":[]} -->

## Phase 8: Convergence

- [ ] T007 Neutralize the hardcoded convergence verdict in `orchestrator/workflow/driver.py` so the convergence gate reports an honest, unprompted outcome per plan: Project Structure / Constitution I (unrequested).
  <!-- harness-task {"requirements":[],"acceptance_criteria":[],"plan_decisions":["D-001"],"dependencies":[],"test_type":"UNIT","allowed_files":["orchestrator/workflow/driver.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->

- [ ] T008 Review and justify or revert the out-of-scope edits shipped in feature commit f76fb5e (`orchestrator/verification/harness.py`, `orchestrator/workflow/quality_gates.py`, `orchestrator/prompts/test_validator.md`, `orchestrator/__main__.py`) per FR-006 allowed_files and plan: Project Structure (unrequested).
  <!-- harness-task {"requirements":["FR-006"],"acceptance_criteria":[],"plan_decisions":["D-001"],"dependencies":["T007"],"test_type":"NOT_AUTOMATABLE","justification":"These edits landed outside every task's allowed_files and require a human decision to revert or document justification; path checks alone cannot judge pipeline-stabilization intent.","alternative_verification":"For each file, decide revert or record a written justification in specs/001-provider-summary/; then run python -m pytest -q, python -m ruff check orchestrator tests, python -m mypy, and python -m compileall -q orchestrator tests and record the results.","allowed_files":[],"tdd_phases":[]} -->
