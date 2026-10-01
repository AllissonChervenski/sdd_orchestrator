# Implementation Plan: `provider-summary`

**Branch**: `001-provider-summary` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-provider-summary/spec.md`

## Summary

Add `python -m orchestrator provider-summary` as a read-only CLI view over the orchestrator's existing provider registry and local capability cache. Use the cached `ProviderCapabilities` data already loaded by `orchestrator.cli._router`; never call `discover_all`, provider `discover()`, `probe`, a CLI, or a live smoke test. Format the same summary data as human-readable text or the canonical `{"providers":[...]}` JSON document. Treat missing/unknown availability as `false` and missing catalogs as a model count of `0`.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: Python standard library (`argparse`, `json`, `dataclasses`); existing orchestrator modules; no new dependencies
**Storage**: Read-only `.orchestrator/capabilities.json`; no writes
**Testing**: pytest; CLI subprocess tests plus unit tests using temporary cache data and patched discovery/probe boundaries
**Target Platform**: Existing Python CLI environments
**Project Type**: Python CLI application
**Performance Goals**: Synchronous local cache read and formatting; no provider startup, network access, or interactive input
**Constraints**: Read-only; no subprocesses, model/API calls, smoke tests, new providers, dependencies, or changes to protected architecture/`doctor` behavior
**Scale/Scope**: One CLI subcommand, summary serialization/formatting, and focused tests; only currently registered providers

## Constitution Check

**Before design**

- I. Python remains the CLI/state authority; provider data is consumed locally. **PASS**
- II. Implementation tasks must use formal RED → GREEN → REFACTOR; RED tests are immutable during GREEN/REFACTOR. **PASS — required execution constraint**
- III. Read-only reporting reuses `PROVIDERS` and cached `ProviderCapabilities`; no discovery or subprocess path. **PASS**
- IV. Keep normal text output and pure JSON stdout; expected errors go to stderr with non-zero status and no raw traceback. **PASS**
- V. Preserve requirement/test traceability in this plan and later task contract. **PASS**
- Protected components and behaviors (ModelRouter, costs, StageRegistry, SkillDispatcher, TDD engine, providers, `doctor`) remain unchanged; no dependencies added. **PASS**

## Research and Design Decisions

Research was resolved by inspecting the current CLI, provider registry, `ProviderCapabilities`, cache loader, `doctor`, and provider adapters. `.specify/extensions.yml` is absent, so there are no pre-plan or post-plan hooks to run.

| ID | Decision | Rationale | Alternatives considered |
|---|---|---|---|
| D-001 | Register the subcommand in `orchestrator/cli.py` using the existing `argparse` subparser pattern. | `orchestrator/__main__.py` already delegates to `cli.main`; this preserves the established entry point and standard argparse rejection of unknown flags. | New module/entry point or custom parser; both add unnecessary structure and risk changing CLI behavior. |
| D-002 | Build the report from the locally loaded capability cache and `PROVIDERS` registry via the existing `_router` data path; do not call discovery. | Registry membership defines known providers; cached `ProviderCapabilities` supplies confirmed availability and models. `_router` reads `.orchestrator/capabilities.json` and does not run provider adapters. | `doctor.discover_all` and adapter `discover()` are rejected because they probe external CLIs and write cache files. A hardcoded provider list is rejected. |
| D-003 | Convert each cached capability to `name=str(provider)`, `available=(cli_available is True)`, and `model_count=len(models)` when the catalog is a list, otherwise `0`. Missing cache entries use the existing default `ProviderCapabilities` (false/empty). | Strict identity comparison prevents unknown/truthy non-bools from being reported as confirmed. Empty/missing catalogs have a stable integer zero. | Inferring availability from model presence/version, or emitting null/unknown values, conflicts with the spec. |
| D-004 | Keep one canonical in-memory report and render from it. JSON has exactly one top-level `providers` key; each item has `name`, `available`, and `model_count`. Text output follows the compact provider/status/count style used by the existing CLI. | A single data path keeps both output formats consistent and makes JSON field types deterministic. | Separate data gathering per output mode risks divergence. |
| D-005 | Catch expected local read/shape errors at the command boundary, write a concise diagnostic to stderr, and return a non-zero exit status; leave argparse's usage error handling intact. | Matches CLI failure containment without swallowing unexpected programmer errors or emitting tracebacks for expected input failures. | Silently treating unreadable or malformed cache as empty would conceal local data failure. |

No unresolved technical clarifications remain. The exact model/report representation and external CLI contract are specified below in this plan because the invocation permits writing only `plan.md`.

## Requirement-to-Decision and Test Traceability

| Requirement | Plan decision | Test approach / evidence |
|---|---|---|
| FR-001; AC-001 | D-002, D-003, D-004: enumerate registered providers; show name, `OK` only for confirmed true, otherwise `UNAVAILABLE`; report integer count. | CLI test with deterministic cache asserts exit 0 and every registered name/status/count appears; test absent cache/catalog and verify zero. |
| FR-002; AC-002 | D-003, D-004: serialize canonical typed report as the only stdout payload in `--json` mode. | Capture stdout, parse with `json.loads`, assert exact top-level/item keys and `type(name) is str`, `type(available) is bool`, `type(model_count) is int`; reject surrounding output. |
| FR-003; AC-004, AC-005; SC-004 | D-002: use cache only; prohibit adapter discovery, probes, subprocesses, network/model calls, smoke tests. | Patch provider constructors/discover methods and probe/subprocess entry points to fail if called; invoke both text and JSON paths and assert success with no calls. |
| FR-004; AC-003 | D-002: derive names from `PROVIDERS`, capabilities/models from existing local cache abstractions. | Supply a registry/cache fixture containing a provider beyond any assumed examples and assert it is included; inspect call boundaries to ensure no duplicate discovery. |
| FR-005; AC-009 | D-005: argparse handles unsupported flags; expected cache I/O/shape failures become stderr diagnostics and non-zero result without traceback. | CLI invalid-flag test checks non-zero, usage on stderr, no traceback; inject unreadable/malformed local data and assert readable diagnostic/no traceback. |
| FR-006; AC-007 | Execute every implementation task RED → GREEN → REFACTOR with Python orchestrator enforcement and immutable RED tests. | Preserve orchestrator evidence that focused tests fail initially for missing command/options, pass after GREEN, and remain passing after REFACTOR; do not edit RED tests during later phases. |
| FR-007; AC-008 | Limit implementation to CLI/report/test surface; do not edit protected components, `doctor`, provider registry/adapters, or dependency declarations. | Review allowed_files and final diff; assert no protected paths changed and dependency manifests are unchanged. Existing `doctor` regression coverage stays in full regression. |
| AC-006; SC-003 | Run focused feature tests and the required full regression suite at the appropriate orchestrated verification gate. | `python -m pytest -q`; all existing and new tests must pass. |
| AC-010; SC-005 | D-003: availability is true only for explicit boolean true; unknown/missing values map to false. | Fixture with absent cache and unknown/non-true status asserts JSON `false` and text `UNAVAILABLE`; assert strict bool type. |
| AC-011; SC-005 | D-003: absent/unusable catalog maps to integer `0`. | Fixture for no models and absent catalog asserts JSON integer `0`, text `0 discovered`, and non-negative integer type. |
| SC-001 | D-001, D-004: synchronous non-interactive subcommand. | Invoke both formats with subprocess input closed and bounded test timeout; assert exit 0 under nominal local data. |
| SC-002 | D-004: stdout contains only JSON in JSON mode. | Parse the entire captured stdout with a standard JSON parser; assert no prefix/suffix output. |

## Data Model and CLI Contract

### `ProviderSummaryItem`

- `name: str`: provider identifier from the existing registry.
- `available: bool`: `True` only when cached `cli_available is True`; otherwise `False`.
- `model_count: int`: length of the cached model list; `0` when absent or unusable.

### `ProviderSummaryReport`

- `providers: list[ProviderSummaryItem]`, in the registry's iteration order.
- JSON contract: `{"providers":[{"name":"agy","available":true,"model_count":14}]}`. Empty registry yields `{"providers":[]}`.
- Text contract: one line per provider with identifier, `OK` or `UNAVAILABLE`, and integer `N discovered`; no live status claims.
- Successful invocations return 0. Unsupported flags use argparse's non-zero usage path. Expected local read failures return non-zero and put a concise diagnostic on stderr. JSON stdout is reserved exclusively for the payload on success.

## Test Design

Place focused tests in `tests/test_provider_summary.py` (final file authorization belongs to `tasks.md`). Cover command registration and both formats, populated and absent cache, unavailable/unknown status, zero models, strict JSON types and purity, invalid flags, local read errors, and the no-discovery/no-subprocess invariant. Use temporary directories or patched local inputs; tests must not require installed provider CLIs or network access. Run focused tests before the full regression suite under the orchestrator's TDD lifecycle.

## Project Structure

### Documentation (this feature)

```text
specs/001-provider-summary/
├── plan.md       # this plan; design, report schema, and traceability matrix
├── spec.md       # feature requirements
└── tasks.md      # generated in the task phase, including allowed_files and TDD contracts
```

### Source Code (repository root)

```text
orchestrator/
├── __main__.py   # existing entry point delegates to cli.main; unchanged
├── cli.py        # add provider-summary parser and handler
├── providers/    # existing PROVIDERS registry; read only
└── config/models.py # existing ProviderCapabilities; read only
tests/
└── test_provider_summary.py # focused unit/CLI tests (planned)
```

**Structure Decision**: Extend the existing single Python CLI in `orchestrator/cli.py`; reuse the current registry/cache abstractions and existing `tests/` layout. No new package, external interface layer, or dependency is required.

## Constitution Check (post-design)

- Local cache and registry only; no provider discovery or writes: **PASS**.
- Pure JSON stdout, stable bool/int defaults, contained expected errors: **PASS**.
- TDD remains an execution gate owned by Python; tests are only added in RED and protected afterward: **PASS**.
- Protected components, `doctor`, provider set, and dependencies remain untouched: **PASS**.
- Requirement-to-decision-to-test links are recorded above for FR-001–FR-007, AC-001–AC-011, and SC-001–SC-005: **PASS**.

## Complexity Tracking

No constitution violations or additional architectural complexity are proposed.
