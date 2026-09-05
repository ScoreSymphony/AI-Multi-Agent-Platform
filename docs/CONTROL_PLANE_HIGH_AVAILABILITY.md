# Control Plane high availability

Issue #89 adds optional Control Plane failover without changing the canonical Task/Run architecture.
The governing invariant is:

> Canonical durable state is not one Control Plane process.

## Supported availability profiles

| Profile | Coordination | Promotion | Intended use |
| --- | --- | --- | --- |
| Single node | none | none | ordinary production baseline |
| Warm standby | single-writer lease/fence | operator-driven | simple redundant Control Plane |
| Active/passive | single-writer lease/fence | automated or operator-driven | advanced HA deployment |
| Active/active | unsupported | n/a | requires future concurrency proof |

The existing single-node deployment remains valid and must not import or initialize HA components.

## Ownership model

Durable/canonical state includes Task/Run/Event history, configured resources, Agent/Team data,
Automation state, security state and migration/version metadata. Coordination records are durable
operational authority records where the chosen HA backend requires them, but their instance IDs are
not canonical product resource IDs.

Ephemeral process state includes caches, local loops, open SSE/WebSocket connections and health
probe state. Those may be reconstructed or reconnected after promotion.

## Leadership and fencing

`CoordinationProvider` is replaceable. It supplies a monotonically increasing fencing epoch. A
Control Plane in HA mode becomes authoritative only while it can prove its current token through the
provider.

Rules:

1. one non-expired leader may own a generation;
2. authority transfer creates a higher epoch;
3. a stale token is rejected even when the old process resumes later;
4. inability to validate coordination fails closed;
5. local wall-clock comparison never proves authority;
6. active/active lifecycle writers are not claimed or emulated.

`InMemoryCoordinationProvider` exists only as a deterministic fixture. It intentionally offers no
multi-host durability.

## Promotion sequence

A safe promotion uses this barrier:

```text
standby
  -> acquire lease / new fencing epoch
  -> promoting
  -> reconcile unfinished durable work and stale ownership
  -> active
```

`PROMOTING` owns the newly acquired epoch but is not ordinary write/dispatch authority. Public
mutation, Automation and new Worker dispatch paths still fail closed. The only additional authority
available in that state is `require_reconciliation_authority()`, which exists for narrowly scoped
recovery side effects such as completing a cancellation that was already durably pending before the
leadership change.

If reconciliation fails, the candidate is fenced and releases the lease when possible. Availability
is not restored by skipping reconciliation.

`FailoverReconciler` remains the generic integration point. `DistributedRuntimeFailoverReconciler`
is the concrete #14 adapter: it invokes the existing `DistributedRuntime.reconcile()` state machine,
validates the same fencing epoch before and after that potentially asynchronous recovery pass, and
reports changed ownership plus expired reservations. It intentionally does not redispatch lost work
during promotion; ordinary fenced #14 failover/redispatch happens only after the Control Plane is
active.

## Authority-bearing runtime boundaries

The optional HA composition does not rely on routing alone. It revalidates authority at runtime
boundaries that can mutate canonical state or cause external effects:

- Control Plane mutation/command authorization;
- the autonomous Automation evaluation loop;
- distributed scheduling/dispatch and failover actions;
- HA Worker transport immediately before Worker dispatch/cancel side effects.

The normal single-node composition does not instantiate these HA gates.

## Worker fencing generation

`AuthorityGatedDistributedRuntime` stops a standby or already-fenced Control Plane before it creates
or dispatches work. That control-side check alone does not close the delayed-message race: an old
leader could pass its check and then have its Worker command delayed until after another instance is
promoted.

For HA Worker transport, `FencedTransportWorkerDispatcher` therefore adds the current
`FencingToken(instance_id, epoch)` to the operational message envelope for `dispatch` and `cancel`.
The token is deliberately not added to canonical `WorkerJobRequest` identity/state.

Dispatch always uses ordinary active-leader authority. Cancel may use a separate
`cancel_authority_check`; HA promotion composition can bind that to
`require_reconciliation_authority()` so a `PROMOTING` candidate may finish an already-durable
cancellation without gaining permission to dispatch new work.

`FencedWorkerTransportEndpoint` validates the supplied token through the same replaceable
`CoordinationProvider` immediately before handing the command to the Worker dispatcher. It rejects:

- missing/malformed fencing evidence;
- source-instance/token identity mismatch;
- stale epochs from an old Control Plane;
- dispatch/cancel while coordination authority cannot be validated.

This avoids a process-local highest-seen-epoch cache whose protection would disappear after Worker
restart. The HA endpoint is optional; the existing Worker transport remains unchanged for non-HA
profiles.

Transport authentication and service identity remain responsibilities of the existing messaging and
Worker security boundaries. Fencing proves current authority generation; it is not a substitute for
cryptographic authentication.

## Worker restart and re-registration

Distributed state restoration already treats persisted liveness conservatively: restored Node and
Worker records begin offline rather than treating old heartbeats as current evidence. During
promotion, `DistributedRuntimeFailoverReconciler` reuses that state to classify unreachable running
ownership as `lost` and to expire reservation leases whose TTL has elapsed.

After promotion, the Worker/Node may re-register with the same existing IDs. Re-registration updates
liveness/registration state; it does not mint replacement Task/Run/Worker identities and it does not
resurrect a reservation that reconciliation already expired. Lost work is not automatically executed
a second time merely because the Worker reappeared.

## Failover behavior by subsystem

| Area | Required behavior |
| --- | --- |
| New API commands | only the current fenced authority may accept authority-bearing mutations |
| Queued work | remains durable and is reconsidered after promotion |
| Running Tasks/Runs | canonical state remains; external execution is reconciled before redispatch |
| Workers/Nodes | reauthenticate/re-register against a logical endpoint; stale reservations are not resurrected |
| Streams | clients reconnect; stream connection identity is ephemeral |
| Automations/timers | only current authority schedules/fires; delivery idempotency still applies |
| Approvals/Verification | durable records survive promotion unchanged |
| Connectors/webhooks | durable delivery/idempotency is reconciled; no blind duplicate side effects |
| Model/tool calls | unresolved calls are reconciled according to provider/execution semantics before retry |
| Notifications | durable dedupe/state remains authoritative across promotion |

## Production backend requirements

A deployment claiming active/passive HA must use persistence/coordination technology whose real
capabilities satisfy the selected profile. At minimum, the coordination authority needs atomic
single-owner acquisition, durable/consistent epoch progression and stale-token rejection under the
backend's documented failover model.

The HA profile must also document:

- replication and failover consistency assumptions;
- network-partition behavior;
- transactional/CAS semantics;
- backup compatibility;
- migration/maintenance behavior;
- how routing discovers the current active instance;
- how Workers reach or otherwise validate the selected coordination authority.

No specific database, broker, cloud, load balancer or Kubernetes component is mandated by the
canonical architecture.

## Operator failover runbook

For a planned failover:

1. stop admitting new authority-bearing work to the current leader;
2. allow/force it to step down and release its lease;
3. promote the standby;
4. require successful reconciliation before routing write traffic;
5. verify the new instance ID, epoch, readiness and security state;
6. allow Workers and clients to reconnect;
7. investigate any stale-fence rejections instead of overriding them.

For an unplanned leader loss:

1. determine that the previous lease can no longer be valid according to the coordination backend;
2. let one standby acquire the next epoch;
3. complete reconciliation;
4. route traffic only after the candidate reports active/ready;
5. treat any returning old process as fenced until it rejoins as a fresh standby instance.

If coordination itself is unavailable, do not force a process into write leadership. Restore the
coordination quorum/service or use a separately documented disaster-recovery procedure that can
prove exclusive authority.

## Backup/restore

HA is not backup. After a disaster restore, stale live leases and runtime reservations must not be
blindly revived. Restore reconstructs canonical state first, then starts HA coordination from a safe
reconciled state according to the selected backend.

## Current implementation boundary

The current #89 implementation provides:

- platform-owned availability/coordination/fencing contracts;
- a deterministic in-memory coordination fixture;
- fail-closed active/passive/warm-standby service semantics;
- explicit `PROMOTING` state and narrow reconciliation authority;
- a concrete distributed-runtime promotion reconciler reusing #14 recovery semantics;
- restart reconciliation of running Worker Jobs without duplicate redispatch;
- stale reservation expiry and same-ID Worker/Node re-registration coverage;
- HA readiness/status projection;
- authority gating for Control Plane mutation paths;
- leadership-gated Automation runtime evaluation;
- authority-gated distributed scheduling/dispatch/failover actions;
- Worker-side stale-epoch rejection for HA dispatch and cancel transport;
- distinct Worker cancel authority for promotion-time durable cancellation recovery;
- deterministic fencing/outage/promotion/Automation/dispatch/Worker-epoch/restart tests.

Before #89 can close, remaining issue-owned work includes an optional production-shaped HA
deployment composition with a suitable shared coordination backend, fuller failover telemetry, and
cross-process/chaos-style acceptance scenarios including duplicate command handling, stream
reconnect and authentication/session continuity.
