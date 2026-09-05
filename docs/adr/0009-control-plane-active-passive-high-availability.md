# ADR 0009 — Control Plane active/passive high availability and fencing

- **Status:** Accepted
- **Issue:** #89
- **Date:** 2026-09-05

## Context

The platform owns canonical Task/Run/Event state independently from one Control Plane process. Single-node operation is a first-class production topology, while advanced deployments need optional failover without making a hostname, VPS, Kubernetes object, load balancer, or one coordination product canonical.

Running multiple HTTP processes is not sufficient: concurrent authority can duplicate lifecycle commands, Worker dispatch, Automation firing, or external side effects. Idempotency reduces duplicate effects but does not by itself prove that arbitrary concurrent writers are safe.

## Decision

### Active/passive is the initial HA model

The supported HA semantics are single-writer active/passive. Warm standby uses the same authority model with operator-driven promotion. Active/active lifecycle writers are not supported until persistence and command concurrency semantics can prove correctness.

The ordinary single-node profile constructs no HA coordination components.

### Process identity is operational

Each Control Plane process has an operational `instance_id` for coordination and observability. It is never a canonical Task, Run, Agent, Artifact, Worker, Project, or other platform resource identity.

### Leadership uses a replaceable coordination contract

`CoordinationProvider` is a platform-owned interface with acquire, renew, release, inspect, and fence-validation operations. Successful acquisition returns a `FencingToken(instance_id, epoch)`. Epochs advance monotonically when authority transfers.

No concrete backend such as etcd, Consul, Redis, Kubernetes Lease, or one database product is canonical. The in-memory provider is a deterministic fixture only and is not a production multi-host HA backend.

### Authority is fenced at side-effect boundaries

An HA instance may mutate canonical state, dispatch work, or run autonomous side-effect loops only while it can prove current authority. A stale epoch remains invalid even if an old process resumes after a pause or partition.

The coordination backend owns lease-time truth. Local wall-clock comparison cannot establish leadership. If authority cannot be validated, the instance fails closed and becomes fenced.

### Promotion requires reconciliation

Promotion order is:

1. inspect prior coordination state;
2. acquire the current/new fencing epoch;
3. reconcile durable unfinished work and stale runtime ownership;
4. become active only after reconciliation succeeds.

A reconciliation failure must not be bypassed to restore availability. The candidate releases leadership on a best-effort basis and remains fenced.

### Backend capability determines safe profiles

A production HA composition must provide the consistency properties required by the chosen profile, including atomic single-owner acquisition, durable monotonic fencing generation, stale-token rejection, defined partition/failover behavior, migration compatibility, and safe backup/restore treatment.

A backend that lacks these properties may remain valid for the single-node profile but must not be advertised as a safe HA coordination backend.

### Routing and streams are deployment concerns

Clients address a logical Control Plane service. Reverse proxies, DNS/service discovery, endpoint lists, or load balancers may route to the active instance, but none becomes canonical platform identity. SSE/WebSocket connections are ephemeral and may reconnect after failover.

### HA does not replace backup/restore

Replication can replicate corruption. Backup/restore remains independently required. Restored installations must reconcile or recreate coordination state instead of reviving stale live leases.

## Consequences

- The canonical kernel remains the lifecycle authority.
- Single-node behavior and dependencies remain unchanged.
- Authority checks belong at real mutation/dispatch/runtime side-effect seams rather than in routing alone.
- Worker dispatch must eventually carry enough fencing generation information for stale Control Plane ownership to be rejected beyond the initiating process.
- Automation scheduling/firing must be leadership-gated.
- Readiness may be false while an HA instance is standby, promoting, or fenced.
- A coordination outage can intentionally reduce availability rather than permitting split-brain writes.

## Alternatives considered

### Active/active writers now

Rejected because current idempotency does not prove arbitrary concurrent writers and autonomous loops safe.

### Make one HA technology canonical

Rejected because it violates deployment/provider neutrality and replacement strategy.

### Rely only on health checks and routing

Rejected because routing cannot fence a paused or partitioned old leader.

### Infer leadership from local time

Rejected because clock skew and process pauses make local lease inference insufficient.

## Affected issues and contracts

- #89 owns this decision and Control Plane HA integration.
- #14 consumes fencing generation for Worker ownership/re-registration where needed.
- #16 projects HA health/failover telemetry.
- #18/#241 gate Automation scheduler authority across leadership changes.
- #40 restore reconciles HA coordination rather than restoring stale leases.
- #41 coordinates migrations/maintenance with multiple instances.
- #43 threat-models split brain, impersonation, and failover attacks.
- #46 adds optional HA acceptance scenarios.
- #240 owns distributed/heterogeneous deployment packaging; HA remains optional.
