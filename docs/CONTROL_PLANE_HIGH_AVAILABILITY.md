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
  -> reconcile unfinished durable work and stale ownership
  -> active
```

If reconciliation fails, the candidate is fenced and releases the lease when possible. Availability
is not restored by skipping reconciliation.

The generic `FailoverReconciler` is the integration point. Concrete reconciliation must eventually
cover the owning subsystems rather than inventing a second recovery model.

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
- how routing discovers the current active instance.

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

The initial #89 slice provides:

- platform-owned availability/coordination/fencing contracts;
- a deterministic in-memory coordination fixture;
- fail-closed active/passive/warm-standby service semantics;
- promotion reconciliation barrier;
- status suitable for later readiness/observability projection;
- deterministic fencing/outage/promotion tests.

Before #89 can close, the remaining issue-owned integrations must connect this authority boundary to
the real Control Plane mutation/dispatch paths, Worker reconnect/reconciliation, Automation
leadership, deployment profile and cross-process integration/chaos tests.
