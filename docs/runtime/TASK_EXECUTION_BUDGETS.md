# Task execution budgets

The Task Execution Budget domain adds a platform-owned admission boundary for bounded autonomous Task execution. It is deliberately separate from Accounting: Accounting remains the sole ledger for canonical usage and cost records, while Task execution budgets own only policy, reservations, runtime counters for non-accounting dimensions, and admission decisions.

## Authority split

- `accounting` owns measured/reported/estimated/unavailable usage and cost truth.
- `execution.budgets` owns per-Task limits, atomic reservations, admission, warning/exhaustion state, and immutable policy revisions.
- `security` remains the authorization and Approval authority for initial budget configuration and later revisions.
- Agents may inspect remaining budget but do not gain authority to grant themselves more budget.

A budget limit chooses one of three consumption sources:

- `accounting`: query canonical #76 `UsageRecord` state by Task, metric type and unit;
- `runtime_counter`: count platform-owned operations such as replans or repairs;
- `clock`: compare the current time with the Task-bound runtime origin.

Estimated accounting records count only when the exact limit opts in. Explicitly unavailable cost data is never converted into a trustworthy zero. Policies choose whether unavailable data blocks, requires Approval, or is explicitly allowed.

## Dimensions and admission points

The canonical dimensions are `model_tokens`, `external_cost`, `model_calls`, `tool_calls`, `runtime_seconds`, `replans`, `parallel_steps`, `retries`, and `repairs`.

The normal public Single-Node composition uses one durable Task-budget authority across:

- canonical model execution;
- canonical capability invocation in the Agent capability turn;
- replanning;
- automatic Verification reviewer model work;
- Verification repair startup.

Model/tool decorators reserve known call-count units before provider execution, release them when execution fails or is cancelled, and reconcile successful work before re-reading canonical Accounting measurements. Replanning and repair use the same Task policy rather than maintaining subsystem-local counters.

`parallel_steps` has concurrency semantics rather than cumulative-count semantics. Its reservation must remain active for the lifetime of the corresponding Step attempt and be released on terminal/cancelled execution; it must not be permanently reconciled as a consumed count. This coordinator-lifecycle binding is therefore distinct from the cumulative `retries` dimension.

## Durable state

The public Single-Node profile persists:

- #76 usage/cost truth in `accounting.sqlite3` when no external Accounting service is injected;
- #902 policy, revision history, reservations and runtime counters in `task-execution-budgets.sqlite3`.

Budget policy revisions cannot reset the original Task runtime origin. Active reservations participate in atomic admission so concurrent branches cannot all observe the same remaining unit and overspend it.

## Control Plane

The deployment registers the extension collection:

- `task-execution-budgets`

and commands:

- `task-budget.configure`
- `task-budget.revise`

The resource projection includes configured limits, policy version/history, consumed/reserved/remaining values, measurement-quality counts, unavailable counts, warnings, blocking dimensions, observation time, and the canonical Task trace reference.

Initial configuration and revisions are exact proposed actions behind #15 Authorization. Agent-originated mutations require an independently approved Approval record even if an otherwise permissive policy would allow Task modification.

## CLI

The existing generic extension CLI exposes the same Control Plane surface without creating a second budget API:

```bash
platform extension show task-execution-budgets <task-id>
platform extension list task-execution-budgets
```

Configure a policy with the canonical command seam:

```bash
platform extension execute task-budget.configure <task-id> \
  --idempotency-key task-budget-initial-v1 \
  --payload '{"limits":[{"dimension":"replans","limit":2,"source":"runtime_counter"}]}'
```

A revision submits the full replacement limit set through `task-budget.revise`; the server advances the immutable policy version and preserves consumed usage/counters.

## Exhaustion semantics

Admission outcomes distinguish `allowed`, `warning`, `blocked`, `approval_required`, `metric_unavailable`, and `exhausted_during_execution`. A call that lands exactly on its limit may finish; the next admission is blocked. Trustworthy post-action usage above the configured limit is reported as exhaustion during that action.

Budget exhaustion is a canonical Task resource condition. Runtime boundaries surface it as `RESOURCE_EXHAUSTED` with the Task ID, action and blocking dimension instead of presenting it as an arbitrary provider failure.
