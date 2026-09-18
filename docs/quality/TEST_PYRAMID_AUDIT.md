# Test pyramid hotspot audit

This audit supports issue #1109. Its purpose is to separate deterministic domain policy from the
real boundaries that still require integration coverage. It is not a rationale for deleting
integration or end-to-end tests.

## Classification rule

Use the lowest test layer that can prove the behavior without hiding the boundary that makes the
behavior meaningful:

| Responsibility | Primary layer | Keep higher-layer evidence for |
|---|---|---|
| Pure deterministic policy/state/ordering | `unit` | representative wiring and lifecycle paths |
| Canonical/provider interface semantics | `contract` | real adapter/upstream translation where needed |
| Persistence/transactions/restart | `integration` | full lifecycle recovery when cross-system |
| HTTP/auth/routing/serialization | `integration` | user-visible vertical flows |
| Worker/process/distributed coordination | `integration` | E2E/conformance paths |
| Whole-platform lifecycle | `e2e` / conformance | final production-shaped evidence |

A large integration file is not automatically a refactor target. The trigger is deterministic logic
whose inputs and outputs are conceptually independent from the infrastructure used to reach it.

## Representative repository audit

| Hotspot | Current responsibility | Classification | Decision |
|---|---|---|---|
| `tests/integration/distributed/test_distributed_runtime.py` + `distributed/scheduler.py` | Worker eligibility/ranking plus registry capacity, reservation, heartbeat and dispatch behavior | **mixed**: pure placement policy + real distributed integration | Extract worker eligibility/scoring/selection into `distributed/placement_policy.py`; keep scheduler/registry/runtime integration coverage intact. |
| `tests/integration/distributed/test_failover_reconciliation.py` + `high_availability/reconciliation.py` | fencing, durable runtime reconciliation and deterministic before/after classification | **mixed**: pure delta classification + real failover/recovery integration | Extract reconciliation-result derivation into `high_availability/reconciliation_policy.py`; keep fencing, persistence and runtime recovery tests as integration. |
| `tests/integration/kernel/test_postgres_kernel_repository.py` | PostgreSQL transactions, durable canonical state and coordination semantics | **infrastructure** | Keep integration-first. Do not replace with mocks or pure repository doubles. |
| `tests/integration/control_plane/test_http_api.py` and neighboring Control Plane suites | real routing/auth/serialization/status/error envelopes | **boundary** | Keep HTTP integration. Domain branches reached through HTTP should also have lower-layer coverage when they do not depend on HTTP semantics. |
| `tests/integration/distributed/test_postgres_coordination_multiprocess.py` | multi-process/PostgreSQL coordination | **infrastructure / full integration** | Keep integration-first; process and database behavior are the subject under test. |
| Bifrost/PipeLock tests under `tests/integration/{models,security,configuration,platform,pipelock}` | real optional upstream/provider behavior and security containment | **adapter/upstream integration** | Keep real integration. Pure mapping helpers may have unit/contract tests, but upstream behavior must remain independently exercised. |
| `tests/integration/application_distribution/` | build/release/security/secret/control-plane composition | **mixed by file** | Keep process, secret, auth and Control Plane boundary tests. Extract only deterministic gate/compatibility calculations when they are otherwise reachable solely through composed infrastructure. Existing unit coverage already owns some model/metadata rules. |
| #46-style whole-platform acceptance/conformance | end-to-end canonical lifecycle | **E2E/conformance** | Keep intact. Unit extraction never substitutes for this evidence. |

## Implemented #1109 extraction seams

### Distributed placement

Before this issue, the scheduler combined two responsibilities:

1. reading mutable registry facts (available resources/concurrency, workers/nodes, reservations);
2. applying deterministic eligibility, rejection and preference-ranking policy.

The second responsibility is now pure. `evaluate_candidate()` receives explicit immutable facts,
`score_candidate()` owns preference weights, and `select_worker()` owns stable score/ID ordering.
`DeterministicScheduler` remains the sole placement/reservation authority and still owns registry,
pressure-admission, telemetry and reservation orchestration.

This makes failures such as capability rejection, resource boundaries, anti-affinity, trust,
network constraints, concurrency exhaustion and ranking precedence observable in `tests/unit`
without starting distributed-runtime fixtures.

### Failover reconciliation

Fencing and `DistributedRuntime.reconcile()` remain integration behavior. The deterministic result
classification now receives explicit before/after dispatch states and reservation IDs and derives:

- changed dispatch count;
- newly lost ownership;
- expired reservations;
- recovered/stale counts;
- stable detail fields.

That classification is unit-testable without coordination leases, persisted runtime state, Workers
or async promotion orchestration.

## CI signal

Python CI preserves the same required coverage while executing stable suite responsibilities as
isolated matrix lanes: unit; contract/architecture/release; integration; and
E2E/performance/regression. Static/package validation runs as a sibling matrix lane. The required
`test` check is an aggregate gate over the complete matrix, so no tier becomes optional.

The serial local fallback remains `pytest`. CI additionally runs
`scripts/ci/verify_pytest_shards.py`, which proves that the union of the non-unit lanes is exactly
the collection selected by `pytest -m "not unit" tests` and rejects missing, unexpected or
duplicated tests. Runtime evidence and budgets are documented in
[`PYTHON_TEST_RUNTIME.md`](PYTHON_TEST_RUNTIME.md).

### Controlled regression evidence

The unit lane runs `scripts/ci/verify_fast_unit_regression_detection.py` immediately after its
focused pytest selection. Other lanes may execute concurrently, but the probe remains an independent
fast signal that does not require an integration fixture to discover the deterministic regression.
The probe:

1. copies the production `src/` tree into a temporary directory;
2. changes only the temporary distributed-placement preferred-worker score from `1000` to `999`;
3. runs the existing focused unit invariant
   `test_preference_score_has_explicit_additive_precedence` against that mutated production copy;
4. succeeds only when that unit test fails for the expected assertion;
5. prints the measured detection time from the current runner instead of encoding a fragile
   hardware-independent runtime budget.

The repository working tree is never mutated by the probe, and no PostgreSQL service, HTTP server,
Worker, subprocess-backed provider or other integration fixture is started to detect the regression.
This supplies executable evidence that a real deterministic production-policy regression is caught
by the fast layer before infrastructure-heavy suites are needed.

The two extracted seams currently add 11 focused unit cases in total: seven placement-policy cases
and four reconciliation-policy cases. The representative integration files for both migrated paths
remain present and continue to prove scheduler/runtime wiring and failover/recovery behavior rather
than duplicating every deterministic branch below them.

## Review guardrail

When adding a branch to an integration-heavy service, ask:

1. Does this branch depend on a database transaction, socket/HTTP behavior, process/Worker state,
   filesystem semantics, external provider behavior or concurrency primitive?
2. If not, can its inputs and result be expressed as canonical values without mocking internals?
3. If yes, put the deterministic decision behind the owning domain/service seam and cover its branch
   matrix in `tests/unit` (or `tests/contract` when the behavior is an interface contract).
4. Keep at least representative integration coverage proving the real boundary still supplies those
   inputs and consumes the result correctly.
5. Never create a duplicate production lifecycle solely to make a unit test possible.

The goal is earlier failure localization with the same or stronger production-shaped confidence.
