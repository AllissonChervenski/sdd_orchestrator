---
description: "SpecKit task checklist with harness metadata"
---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md and spec.md; use research.md, data-model.md, and contracts/ when present.

Keep the SpecKit checklist format exactly: `- [ ] T001 [P?] [US1?] Description with file path`.
Organize phases by setup, foundation, each user story, and polish. Include story goals,
independent test criteria, dependencies, parallel examples, and implementation strategy.

For every executable checklist item, put one machine-readable HTML comment directly
on the next line. The comment is harness metadata; the checklist line remains the
authoritative SpecKit task and its ID must not be repeated in the comment.

```text
- [ ] T010 [US1] Implement the queue in src/queue.py
  <!-- harness-task {"requirements":["FR-001"],"acceptance_criteria":["AC-001"],"plan_decisions":["D-001"],"dependencies":[],"test_type":"UNIT","allowed_files":["src/queue.py"],"tdd_phases":["RED","GREEN","REFACTOR"]} -->
```

Use exact IDs from spec.md and plan.md. `dependencies` contains earlier checklist
IDs. `allowed_files` contains narrow relative production paths; test files are
declared by the RED test designer. For behavioral work, include the three phases
in the order shown. The Python harness executes those phases for the one SpecKit
task; do not create three duplicate checklist items. Use `NOT_AUTOMATABLE` only
with `justification` and `alternative_verification` in the comment. Preserve all
SpecKit task generation rules from the installed speckit-tasks skill.

## Phase 1: Setup

**Purpose**: Shared project preparation.

## Phase 2: Foundational

**Purpose**: Prerequisites for user stories.

## Phase 3+: User Stories

For each story, include **Goal**, **Independent Test**, its task checklist, and a
checkpoint. Tests requested by the specification precede implementation.

## Final Phase: Polish & Cross-Cutting Concerns

## Dependencies & Execution Order

## Parallel Examples

## Implementation Strategy
