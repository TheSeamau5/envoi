# Envoi ExecPlans

This file defines how to write and maintain an execution plan ("ExecPlan") for
the Envoi monorepo. An ExecPlan is a living, self-contained design and delivery
document that a coding agent can follow from research through verification
without relying on prior chat context.

Treat the reader as new to this repository. The plan must stand on its own with
only the current working tree and the ExecPlan file in hand.

## When to use an ExecPlan

Use an ExecPlan when the work is large enough that progress, decisions, and
validation need to survive across multiple turns or sessions. In this repo that
usually means:

- cross-package work,
- public API or CLI surface changes,
- trace, log, schema, replay, or checkpoint contract changes,
- data flow changes between S3, local cache/materialized state, and the web UI,
- example task or environment changes that affect the SDK, runner, or dashboard,
- any refactor with meaningful unknowns or more than one milestone.

Store task-specific ExecPlans in `.codex/plans/<yyyy-mm-dd>-<slug>.md`.

## How to use an ExecPlan

When authoring an ExecPlan, read this entire file first. Start from the
required structure below and fill in concrete repository facts as you research.
Do not leave hidden assumptions in your head. Put them in the plan.

When implementing from an ExecPlan, do not stop to ask for "next milestones"
unless you are blocked on an external decision. Move to the next milestone,
update the plan as you go, and keep the plan truthful about current status.

When revising an ExecPlan, treat it as the durable source of truth. Record
decisions, failed attempts, discoveries, and validation results so a new agent
can restart from the plan alone.

## Non-negotiable requirements

Every ExecPlan must be self-contained. If success depends on repo structure,
commands, contracts, or assumptions, spell them out in the plan.

Every ExecPlan must be a living document. Update it when progress is made, when
discoveries change the design, and when validation fails or passes.

Every ExecPlan must describe a working outcome, not just a set of edits. The
reader should understand what behavior changes, how to run it, and what proves
it works.

Every ExecPlan must define repo-specific terms that matter to the task. In this
repo, be precise about `part` versus `turn`, trace artifacts, checkpoint
commits, environment/task boundaries, and any package-level contract you rely
on.

Every ExecPlan must include exact validation commands and the expected outcome
for each one. "Run tests" is not enough.

## Repo context that plans must account for

Envoi is a monorepo with both Python and TypeScript surfaces.

- `packages/envoi` is the SDK.
- `packages/code` is the orchestrator/runner layer.
- `packages/cli` is the unified `envoi` CLI and routes into the other Python
  packages.
- `packages/web` is the dashboard and reads trajectory data through S3, DuckDB,
  local cache/materialized state, and runtime UI rendering.
- `examples/<name>/task` and `examples/<name>/environment` are real integration
  surfaces, not throwaway demos.

Dependency direction matters:

- `envoi-cli -> envoi-code -> envoi`
- `packages/web` consumes trajectory/runtime artifacts produced by the Python
  stack.

Plans must name every affected package and every downstream consumer that could
break if the contract changes.

## Required content for every ExecPlan

Each ExecPlan must include the following sections.

### Title

Use a short, outcome-oriented title. Name the user-visible capability or the
system behavior being changed.

### Purpose

Explain why the work matters from a user's perspective. State what someone can
do after the change that they could not do before, and how they will see the
result.

### Repo context and scope

Name the affected packages, entrypoints, artifacts, examples, and data flows.
State what is in scope and what is explicitly out of scope.

### Current behavior

Describe the current implementation and the current failure or limitation in
plain language. Include the files and commands needed to observe it.

### Target design and invariants

Describe the intended end state and the invariants that must remain true. This
section should make it clear what may change and what must not regress.

For Envoi, call out any invariant around:

- `part` and `turn` semantics,
- trace/log artifact contracts,
- git checkpoint behavior,
- task/environment loading,
- CLI routing,
- S3/cache/UI parity in the dashboard,
- package-specific instruction files such as `packages/web/AGENTS.md`.

### Milestones

Break the work into concrete milestones. Each milestone should end with a real
verification point and should be small enough to complete without mixing too
many unrelated concerns.

If a milestone introduces uncertainty, include a small proof-of-concept or
research milestone first.

### Validation

List the exact commands that prove the work. Include both narrow checks and any
required downstream checks.

Use repo-native commands:

- Python workspace checks use `uv run ...`.
- CLI checks use direct `envoi ...` commands, never `uv run envoi ...`.
- Web checks use `pnpm --dir packages/web ...`.

If `packages/web` or `c-compiler` dashboard flows are affected, the plan must
say how S3, local cache/materialized data, and runtime UI will be verified
together.

### Progress

This section is mandatory and must use checkboxes.

- `[ ]` not started
- `[~]` in progress
- `[x]` complete

Update the checklist at every stopping point. Make the next unfinished item
obvious.

### Decisions

Record design decisions and why they were made. This avoids oscillation and
lets a new agent continue with the same reasoning.

### Surprises and discoveries

Record anything the repo taught you that changed the plan: hidden coupling,
missing tests, stale docs, runtime behavior, data mismatches, or package-level
constraints.

### Risks and rollback

State the highest-risk parts of the change and how to back out or contain the
impact if validation fails late.

## Writing style

Write in plain prose. Prefer short paragraphs over giant bullet dumps. Use
lists where they add clarity, but do not turn the entire plan into a checklist.
The `Progress` section is the only section that must be checklist-shaped.

When an ExecPlan lives in its own `.md` file, do not wrap the whole file in an
outer code fence. Prefer indented command or output examples inside the plan.

Do not assume the reader remembers earlier conversation. Repeat the key repo
facts, commands, and expected observations inside the plan itself.

## Monorepo validation expectations

Plans must validate both the changed package and any dependent surfaces.

- `packages/envoi`: usually `uv run pytest packages/envoi/tests` and relevant
  type checks.
- `packages/code`: usually `uv run pytest packages/code/tests`, plus targeted
  coverage for orchestrator, parsing, storage, or schema changes.
- `packages/cli`: relevant `envoi ...` smoke coverage, plus tests for the
  package the CLI routes into.
- `packages/web`: `pnpm --dir packages/web lint`, `pnpm --dir packages/web test`,
  and any live verifier required by `packages/web/AGENTS.md`.
- `examples/`: the narrowest real `envoi code ...` or `envoi deploy ...` flow
  that exercises the changed path.

If a shared contract changed, the plan must say how downstream consumers are
checked before the work is called done.

## ExecPlan skeleton

Use this as the starting structure for a task-specific ExecPlan.

# <Outcome-oriented title>

## Purpose

<Why this matters, what changes for the user, and how to observe the win.>

## Repo context and scope

<Affected packages, examples, artifacts, entrypoints, and non-goals.>

## Current behavior

<Current implementation, current limitation, and how to reproduce it.>

## Target design and invariants

<Intended end state, constraints, and contracts that must remain true.>

## Milestones

<Milestone 1 with the files, commands, and expected outcome.>

<Milestone 2 with the files, commands, and expected outcome.>

## Validation

<Exact commands and expected results, including downstream checks.>

## Progress

- [ ] Milestone 1
- [ ] Milestone 2
- [ ] Validation complete

## Decisions

- <Decision and rationale>

## Surprises and discoveries

- <New fact and how it changes the plan>

## Risks and rollback

<Highest-risk failure modes and how to back out or contain them.>
