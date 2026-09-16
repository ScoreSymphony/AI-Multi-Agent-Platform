# Issue #902 — per-Task autonomous execution budget preparation

Status: **implementation-prepared on `issue-902-task-execution-budgets`; no acceptance claim yet**.

This document records the live architectural baseline and the implementation boundaries for #902. It is intentionally a preparation/handoff artifact: it does not claim that model/tool/replan/runtime admission, parallel reservation durability, authorization/Approval overrides, UI/CLI, or the #889 end-to-end fixture are complete.

## Branch baseline

The branch was created from `main` commit:

`d3a6c2e17c2ef80fb118d43691bf0cca703b6b8e`

That `main` revision already contains the #76 accounting foundation, #15 authorization/Approval infrastructure, the canonical Agent capability-turn path, planning/replanning support, Control Plane usage projections, and the existing UI/CLI usage surfaces that #902 must extend rather than replace.

## Source-of-truth decisions

### Accounting remains #76-owned

`src/ai_multi_agent_platform/accounting/models.py` already defines:

- `UsageRecord` with measured/reported/estimated/unavailable quality;
- `UsageScope` with Task/Run/Agent/Capability/model/provider/Worker/Node attribution;
- `UsageBudget` with metric, unit, scope, soft/hard kind, action, warning fraction and estimated-value policy;
- `BudgetState` and threshold events.

`AccountingService` already owns durable usage aggregation and budget-state evaluation. #902 must consume this state. It must not create a second token/cost ledger or reinterpret unavailable values as zero.

### Runtime enforcement is new #902 authority

#902 should add a platform-owned runtime admission/enforcement layer above #76. That layer owns:

- pre-action admission decisions;
- atomic reservation/claim semantics for concurrent branches;
- Task wall-clock deadline checks;
- canonical exhaustion/block/warning/approval-required outcomes;
- reservation release/reconciliation;
- override/increase history and authorization handoff;
- projection of configured, consumed, reserved and remaining values.

The enforcement service may maintain durable reservation/runtime-counter state, but consumed token/cost/tool/model usage must continue to reconcile against #76 canonical accounting where that metric is available there.

### Existing replanning budget must converge into #902

`src/ai_multi_agent_platform/planning/replanning.py` currently enforces `ReplanPolicy.max_replans` independently by counting non-initial planning proposals.

That logic is useful canonical evidence, but after #902 it must no longer be a separate budget authority. The planning path should call the #902 admission service before a replan and preserve the existing proposal history as the canonical/reconstructible replan count input.

## Canonical Task budget model

Add a provider-neutral Task budget policy that can express at least:

- total model tokens / provider usage units where semantically comparable;
- external monetary cost, with currency and measurement-quality policy;
- model-call count;
- Capability/Tool invocation count;
- Task wall-clock runtime/deadline;
- replanning count;
- optional narrower Run/Step/Agent-scoped overrides or limits without changing the Task-level authority.

The Task-level policy should reference canonical accounting metric/unit pairs rather than inventing duplicate usage semantics. Count/runtime-only dimensions that are platform-owned may use canonical runtime counters when no #76 measurement is appropriate.

Recommended conceptual split:

```text
TaskBudgetPolicy
  ├─ task_id
  ├─ limits[]
  │   ├─ dimension
  │   ├─ limit
  │   ├─ metric_type/unit when #76-backed
  │   ├─ estimated/unavailable handling
  │   ├─ warning threshold
  │   └─ action on exhaustion
  ├─ started_at/deadline
  ├─ version
  └─ provenance

TaskBudgetRuntimeState
  ├─ consumed      <- #76 / canonical counters
  ├─ reserved      <- #902 durable reservation store
  ├─ remaining
  ├─ warning/exhaustion state
  └─ active reservations[]
```

Do not put mutable consumed/reserved counters directly into the immutable `Task` domain object. The Task may carry/reference its configured budget policy, while runtime state remains separately durable and reconstructible.

## Admission outcomes

Use explicit canonical outcomes rather than provider errors:

- `allowed`;
- `warning`;
- `blocked`;
- `exhausted_during_execution`;
- `approval_required`;
- `metric_unavailable`;
- `budget_increased` / `override_applied` as audited administrative outcomes.

A budget rejection should surface through `ErrorCode.RESOURCE_EXHAUSTED` or a dedicated canonical budget reason carried in Task/Step wait/failure metadata, not as an ordinary model/provider/tool failure.

## Atomic reservation contract

Parallel Agent/Step execution must not race on the same remaining budget.

A reservation operation should atomically evaluate:

`canonical consumed + active reservations + requested amount <= configured limit`

and either create a reservation or reject the action.

Minimum reservation attributes:

- reservation ID;
- Task ID;
- budget dimension / metric-unit identity;
- requested quantity;
- action kind (`model_call`, `tool_call`, `parallel_step`, `replan`, `repair`, etc.);
- Run/Step/Agent/AgentRun references when available;
- creation/expiry timestamps;
- state (`active`, `released`, `reconciled`, `expired`);
- correlation/causation/provenance.

On cancellation/failure before consumption, release the claim. After successful consumption, reconcile the reservation against the authoritative #76 usage/canonical runtime counter rather than retaining it as a second permanent usage ledger.

Reservation storage must be restart-safe before #902 can satisfy acceptance.

## Concrete integration points

### Model calls

Primary current hook:

`src/ai_multi_agent_platform/agents/capability_turn.py`

Before `ModelRuntime.generate_canonical(...)`:

1. check Task runtime deadline;
2. reserve a model-call slot;
3. reserve bounded token/cost estimates only when policy and trustworthy estimate semantics permit it;
4. block/require Approval before provider invocation when necessary.

After the response/failure:

- reconcile/release reservations;
- let #76 remain the authoritative recorded usage/cost source;
- immediately enforce post-call exhaustion when actual provider usage was unknowable before dispatch.

Other direct `ModelRuntime` consumers must either pass through the same enforcement seam or prove they are non-Task/non-autonomous paths.

### Capability / Tool calls

The same `AgentCapabilityTurn` iterates model-requested tool calls and invokes `CapabilityInvoker`.

Before each invocation reserve/check the shared Task tool-call budget. Do not put the authority in the Agent or model-generated tool-call payload. The eventual anti-bypass design should place a reusable #902 hook at/around the canonical capability invocation path so future callers cannot skip enforcement by bypassing `AgentCapabilityTurn`.

### Replanning

Primary hook:

`src/ai_multi_agent_platform/planning/replanning.py`

Replace the independent `ReplanPolicy.max_replans` enforcement authority with a #902 admission call while preserving existing proposal history as canonical count evidence. Initial planning must not consume a replan allowance unless policy explicitly defines otherwise.

### Retry / repair

Admission must be called before autonomous retry/repair cycles, especially Verification repair flows. A failed action must not automatically receive another spend attempt after the Task budget is exhausted.

### Optional/parallel Step dispatch

The scheduler/coordinator must claim shared count/estimated-resource allowance atomically before dispatching additional optional or parallel work. This is the race-sensitive integration that proves multiple Agents do not each see the same full remaining allowance.

### Runtime deadline

Wall-clock budget belongs to Task runtime policy, not provider timeout handling. Admission should reject new autonomous work after the Task deadline even if the next provider/tool call has its own lower-level timeout.

## Authorization and Approval

#15 remains authoritative.

Budget mutation/override commands must:

- require canonical authorization on the Task/budget resource;
- allow an Agent to request an increase but never grant its own request;
- support `require_approval` policy;
- bind Approval to the exact proposed new budget/version;
- append immutable audit/provenance history;
- preserve already-consumed usage when limits change.

No budget override token/flag may be accepted directly from an Agent/model/tool payload.

## Control Plane / Web / CLI

Existing #76 `usage-budgets` resources expose accounting budget state. #902 should add a Task-oriented runtime projection rather than overload the accounting resource with reservation/execution authority.

Required projection fields should include:

- Task ID and budget policy version;
- each dimension's configured limit;
- consumed quantity and measurement quality/source;
- active reserved quantity;
- remaining amount;
- warning/exhaustion state;
- unavailable/estimated enforcement semantics;
- deadline/runtime remaining;
- last blocking reason;
- override/increase provenance;
- links/references to #76 usage data and, when available, #901 trace nodes.

Web and CLI must consume the same Control Plane projection.

## Local-first semantics

The reference implementation must work with local/self-hosted models and no paid API.

- model/tool call counts, runtime and replan limits remain enforceable;
- token limits apply when the local provider exposes trustworthy usage;
- external monetary cost is unavailable unless the configured local cost model actually defines it;
- unavailable monetary cost must not be silently converted to zero unless zero is the truthful configured meaning.

## Implementation slices

Recommended implementation order for this branch:

1. canonical Task budget policy, decisions, reservation and snapshot types;
2. durable reservation/runtime-state repository with atomic compare-and-reserve semantics;
3. `TaskBudgetEnforcementService` consuming #76 accounting plus canonical runtime counters;
4. model-call and tool-call admission hooks;
5. replan and Verification repair admission hooks;
6. parallel Step/Agent reservation integration;
7. authorization/Approval-protected budget mutation commands and audit history;
8. Control Plane Task-budget resource/commands;
9. CLI/Web projection;
10. #889 deterministic three-Agent exhaustion fixture;
11. documentation and complete restart/race/quality test matrix.

## Required test mapping

| #902 requirement | Prepared test target |
| --- | --- |
| token limit before/after model call | pre-call reservation plus post-call canonical #76 reconciliation |
| model-call count | atomic one-slot reservation |
| tool-call count | shared Task reservation around canonical invocation |
| runtime deadline | reject admission after deadline without invoking provider/tool |
| replan limit | existing proposal history consumed through #902 authority |
| parallel race | concurrent reserve calls against one remaining slot; exactly one succeeds |
| cancellation | unused active reservation is released |
| estimated/unavailable cost | explicit policy matrix, never implicit zero/exactness |
| authorized increase | #15 allow/Approval path + immutable history |
| Agent self-increase | denied regardless of model output |
| local model/no money metric | non-cost dimensions continue to enforce |
| restart | reservation/runtime state survives and reconciles deterministically |
| #889 | three-Agent fixture terminates with canonical budget reason |

## Known conflict / regression hotspots

- `accounting/models.py` and `accounting/service.py`: do not broaden #76 into runtime authority;
- `planning/replanning.py`: remove duplicate authority only after #902 service is composed;
- `agents/capability_turn.py`: keep provider-neutral model/tool execution and existing safety gates intact;
- capability anti-bypass paths: ensure non-Agent callers cannot skip #902;
- Task lifecycle: budget exhaustion must not masquerade as provider failure;
- Control Plane usage resources: preserve #76 API compatibility;
- frontend Usage/Limits surfaces: avoid introducing a second inconsistent representation of accounting truth;
- restart/recovery: active reservations require explicit recovery/expiry/reconciliation semantics.

## Dependency assessment

Hard implementation foundations already completed on `main`:

- #76 canonical usage/resource/cost accounting;
- #15 identity/authorization/Approval boundaries.

Integration/acceptance relationships:

- #889 supplies the multi-Agent golden-path fixture that #902 must extend with deterministic exhaustion;
- #901 is the trace/read experience that should correlate Task-budget state once its projection is available, but #902 backend enforcement must not depend on #901 becoming the authority.

## Acceptance rule for this branch

Do not mark #902 complete merely because a `TaskBudget` dataclass or UI limit field exists. Completion requires all of the following together:

- platform-owned pre-action enforcement;
- safe parallel reservations;
- #76-backed usage/cost truth;
- explicit missing/estimated semantics;
- canonical exhaustion reason;
- restart-safe state;
- authorized/audited increases;
- local-first operation;
- Control Plane/Web/CLI visibility;
- deterministic #889 multi-Agent stop behavior.
