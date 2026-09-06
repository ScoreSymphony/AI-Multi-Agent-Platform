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
| Streams | clients reconnect using durable event cursors; connection identity is ephemeral |
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

## Provider-neutral active/passive deployment profile example

The #89 deployment deliverable is a logical profile, not a mandate for one infrastructure product.
A conforming advanced deployment may be arranged as follows:

```text
clients / CLI / UI
        |
        v
logical Control Plane endpoint
(reverse proxy, DNS/service discovery, or endpoint failover list)
        |
        +-------------------+
        |                   |
        v                   v
 Control Plane A       Control Plane B
 active or standby     active or standby
        |                   |
        +---------+---------+
                  |
        +---------+---------+
        |                   |
        v                   v
shared/replicated       shared coordination
canonical durable      authority implementing
state contracts        CoordinationProvider
        |
        v
Workers / Nodes / external adapters
```

Conformance rules for this profile:

1. both Control Plane processes use the same logical canonical persistence contracts; process-local
   state is never promoted to canonical state;
2. both use one shared coordination authority capable of atomic acquisition, monotonic fencing and
   stale-token rejection;
3. routing sends authority-bearing requests only to an instance that reports `active` and ready;
4. standby, promoting and fenced instances remain unavailable for authority-bearing mutations;
5. Workers use a logical/discoverable Control Plane endpoint and HA Worker transport validates the
   current fencing generation before side effects;
6. clients reconnect streams using the canonical durable cursor rather than process-local connection
   identity;
7. authentication, authorization, revocation and audit state come from the same durable security
   contracts used before promotion;
8. the concrete persistence, coordination and routing products are deployment choices and must
   document how they satisfy the capability requirements above.

A deployment can place A and B on separate machines, VMs, containers or other hosts. Their physical
hostnames and infrastructure IDs remain operational metadata. Concrete packaged multi-host examples
and infrastructure automation may evolve under deployment work such as #240 without changing this
#89 contract.

### Redundancy health interpretation

Per-instance `/health` exposes mode, role, leader, epoch, coordination availability, promotion count,
lease/renewal timing and reconciliation/error state. Deployment monitoring derives redundancy from
those provider-neutral instance facts: an active/passive installation is degraded when its active
instance is healthy but no independent standby is reachable/eligible, and unavailable for writes when
no instance can prove active authority. This aggregate is deployment state, not canonical Task/Run
state; #89 deliberately does not infer an imaginary standby from the leader lease alone.

## Operator failover runbook

For a planned failover:

1. stop admitting new authority-bearing work to the current leader;
2. allow/force it to step down and release its lease;
3. promote the standby;
4. require successful reconciliation before routing write traffic;
5. verify the new instance ID, epoch, readiness and security state;
6. allow Workers and clients to reconnect;
7. retry interrupted client commands with the original idempotency key;
8. resume streams from the last durable event cursor;
9. verify existing sessions/credentials and authorization policy on the promoted instance;
10. investigate any stale-fence rejections instead of overriding them.

For an unplanned leader loss:

1. determine that the previous lease can no longer be valid according to the coordination backend;
2. let one standby acquire the next epoch;
3. complete reconciliation;
4. route traffic only after the candidate reports active/ready;
5. let Workers re-register with their existing logical identities;
6. resume clients from durable command/event state;
7. treat any returning old process as fenced until it rejoins as a fresh standby instance.

If coordination itself is unavailable, do not force a process into write leadership. Restore the
coordination quorum/service or use a separately documented disaster-recovery procedure that can
prove exclusive authority.

## Backup/restore

HA is not backup. After a disaster restore, stale live leases and runtime reservations must not be
blindly revived. Restore reconstructs canonical state first, then starts HA coordination from a safe
reconciled state according to the selected backend.

## Issue #89 acceptance matrix

The acceptance criteria are satisfied by the following platform contracts and tests:

| Acceptance criterion | Evidence |
| --- | --- |
| Single-node production remains supported without HA dependencies | `test_issue_89_control_plane_ha.py::test_single_node_remains_active_without_ha_coordination` plus #39 single-node smoke |
| Process/host identity is not canonical | ADR 0009, this ownership model, Worker restart/re-registration identity assertions |
| Active/passive or warm-standby reference path | `ControlPlaneFailoverService`, `InMemoryCoordinationProvider`, simultaneous acquisition and promotion tests |
| Split-brain/stale leader fails closed | stale-leader, simultaneous-acquisition, Worker-epoch and coordination-outage tests |
| Failover does not duplicate canonical Tasks/Runs | final duplicate-command promotion fixture plus running-work restart reconciliation |
| Workers reconnect/re-register without changing task logic | `test_restart_promotion_reconciles_running_work_and_preserves_worker_identity` |
| Pending durable work reconciles after leader loss | `DistributedRuntimeFailoverReconciler` and restart/stale-reservation tests |
| Security/authentication/authorization survives promotion | final persisted authentication/session/authorization continuity fixture |
| Coordination backend is replaceable | `CoordinationProvider` protocol; in-memory implementation remains only a deterministic fixture |
| Deployment remains provider/hardware/Kubernetes neutral | provider-neutral profile above and backend capability requirements |

The required test plan maps as follows:

| Required test | Evidence |
| --- | --- |
| active instance crash/promotion | `test_active_passive_promotion_fences_stale_old_leader` and restart promotion fixture |
| stale old instance writes after promotion | `test_active_passive_promotion_fences_stale_old_leader`; Worker transport stale-epoch tests |
| simultaneous acquisition/fencing | `test_simultaneous_acquisition_yields_exactly_one_active_instance` |
| Control Plane restart with running task | `test_restart_promotion_reconciles_running_work_and_preserves_worker_identity` |
| duplicate command during failover | `test_duplicate_command_replay_after_promotion_does_not_duplicate_task_or_run` |
| Worker reconnect/re-register | `test_restart_promotion_reconciles_running_work_and_preserves_worker_identity` |
| stale reservation reconciliation | `test_distributed_promotion_reconciler_expires_stale_reservations` plus restart fixture |
| Automation duplicate prevention | `test_automation_ticks_follow_current_leadership` |
| client stream disconnect/reconnect | `test_client_stream_reconnect_after_promotion_resumes_from_durable_cursor` |
| authentication/session continuity | `test_authentication_session_continuity_survives_control_plane_promotion` |
| coordination backend unavailable | `test_coordination_outage_fails_authority_closed` |
| single-node mode without HA components | `test_single_node_remains_active_without_ha_coordination` |

## Completed #89 implementation boundary

Issue #89 now provides:

- ADR 0009 for the active/passive HA decision and fencing model;
- platform-owned availability/coordination/fencing contracts;
- deterministic active/passive/warm-standby reference semantics;
- single-node operation with no HA dependency;
- explicit `PROMOTING` state and narrow reconciliation authority;
- concrete distributed-runtime promotion reconciliation reusing #14 recovery semantics;
- restart reconciliation of running Worker Jobs without duplicate redispatch;
- stale reservation expiry and same-ID Worker/Node re-registration;
- Control Plane, Automation, distributed-runtime and Worker-transport authority gates;
- Worker-side stale-epoch rejection for delayed dispatch/cancel messages;
- durable duplicate-command replay across promotion;
- durable stream cursor resume across Control Plane replacement;
- persisted authentication/session/authorization continuity across promotion;
- HA readiness/status and backend-neutral observability;
- backend capability requirements, provider-neutral deployment profile and operator failover runbook;
- deterministic integration/chaos-style coverage for the complete required test matrix.

This completes the #89 architecture and acceptance contract. It does not make active/active safe, make
HA mandatory, choose a production database/coordination vendor, or replace backup/restore. Concrete
infrastructure packaging can add conforming implementations without reopening canonical HA semantics.
