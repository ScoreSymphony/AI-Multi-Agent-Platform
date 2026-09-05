# ADR 0008 — Control Plane active/passive high availability and fencing

- **Status:** Accepted
- **Issue:** #89
- **Date:** 2026-09-05

## Context

The platform already owns canonical Task/Run/Event state and recovery independently from
Hermes, Forge and deployment infrastructure. Single-node operation is a first-class production
topology. Advanced deployments also need to survive loss or replacement of the active Control
Plane process without making a hostname, VPS, Kubernetes object or one coordination product the
canonical lifecycle authority.

Running two HTTP processes does not by itself provide safe high availability. Concurrent
authority can duplicate dispatch, Automation firing or lifecycle commands even when individual
commands are idempotent. The platform therefore needs an explicit single-writer authority model
before it can claim Control Plane failover.

## Decision

### 1. Initial HA support is active/passive

The first supported HA semantics are single-writer active/passive. A warm standby is the same
authority model with operator-driven rather than automatic promotion. Active/active lifecycle
writers are **not supported** until persistence and command concurrency can prove correctness.

The ordinary single-node profile does not construct or depend on HA coordination.

### 2. Process identity is operational, not canonical

Every Control Plane process receives an operational `instance_id`. It identifies one process
lifetime for coordination and observability only. It must never become a canonical Task, Run,
Agent, Artifact, Worker or Project identity.

### 3. Leadership is a replaceable lease with a monotonic fencing epoch

`CoordinationProvider` is a platform-owned boundary with `acquire`, `renew`, `release`, `inspect`
and `assert_fence` operations. A successful acquisition returns a `FencingToken(instance_id,
epoch)`.

The epoch is monotonically increasing whenever authority transfers after release or expiry.
Authority-bearing operations in HA mode must validate the current fencing token immediately before
they mutate canonical state or emit a side effect whose ownership matters.

No concrete backend such as etcd, Consul, Redis, a Kubernetes Lease or one database product is
canonical. The in-memory implementation is only a deterministic contract/integration fixture and
is not a multi-host HA backend.

### 4. The coordination backend owns lease-time truth

Lease timestamps are observational. A Control Plane process must not decide that it is still
leader merely from its local wall clock. The coordination provider is the authority that validates
expiry and fencing. If that validation is unavailable, authority fails closed.

This avoids relying on synchronized application clocks for split-brain prevention.

### 5. Promotion has a reconciliation barrier

Promotion order is:

1. inspect the last known coordination epoch;
2. acquire a new/current lease;
3. reconcile durable unfinished work and stale runtime ownership against that epoch;
4. become `active` only after reconciliation succeeds.

If reconciliation fails, the service releases leadership on a best-effort basis and enters the
`fenced` role. It must not accept authority-bearing work merely to restore availability quickly.

### 6. Coordination loss fences authority

An active instance that cannot validate or renew its token becomes `fenced`. A stale old instance
cannot regain authority using its old epoch after another instance is promoted. Standby instances
remain able to expose non-authoritative health/status according to deployment policy, but must not
perform writes or dispatch solely because the previous leader appears unreachable locally.

### 7. Backend capabilities define which HA profile is safe

A production coordination/persistence composition that claims HA must provide, as applicable:

- durable shared or correctly replicated state;
- atomic compare-and-set/transactional single-owner acquisition;
- monotonic fencing generation;
- consistency strong enough that stale leaders cannot successfully validate old authority;
- defined behavior during network partitions and backend failover;
- migration compatibility with multiple deployed instances;
- backup/restore behavior that does not blindly resurrect stale live leases.

A backend that lacks those properties remains valid for the single-node profile and must not be
advertised as a safe HA coordination backend.

### 8. Routing is deployment metadata

Clients address a logical Control Plane service. Reverse proxies, load balancers, DNS/service
discovery or endpoint lists may route to the active instance, but none is part of canonical Task or
Run identity. Client streams may reconnect after failover.

### 9. HA does not replace backup/restore

Replication can replicate corruption. Backup/restore remains independently required. Restored HA
installations reconcile or recreate coordination state; they do not blindly restore an old active
lease as live authority.

## Consequences

- The canonical Task/Run/Event kernel stays the only lifecycle authority.
- Single-node operation keeps its current architecture and dependencies.
- Authority-bearing integrations need an explicit HA gate rather than scattered `is_leader` flags.
- Worker dispatch, Worker callbacks, Automation scheduling and other external side effects must
  eventually propagate/check the fencing epoch where stale ownership could otherwise survive.
- A coordination outage can reduce availability by design; it cannot silently trade consistency
  for split-brain writes.
- Operational instance/epoch metadata can be projected into readiness and observability without
  contaminating canonical resource identity.

## Alternatives considered

### Active/active Control Plane writers now

Rejected. Existing idempotency protects repeatable commands but does not prove arbitrary concurrent
writers, scheduler loops and external side effects are exactly-once or conflict-free.

### Make Kubernetes/etcd/Redis/Consul canonical

Rejected. It would violate provider and deployment neutrality and make HA technology part of the
platform domain.

### Use only health checks and a load balancer

Rejected. Routing does not fence a paused or partitioned old leader and therefore cannot prevent
split brain.

### Use local process time to decide leadership

Rejected. Clock skew and pauses make local lease inference insufficient. The coordinator must
validate authority.

## Affected issues and contracts

- #89 owns this decision and Control Plane HA integration.
- #14 consumes fencing generation for Worker ownership/re-registration where needed.
- #16 projects HA health, leadership and failover telemetry.
- #18/#241 must gate Automation scheduler authority across leadership changes.
- #40 restore must reconcile HA coordination state rather than restore stale live leases.
- #41 must coordinate migrations/maintenance with multiple instances.
- #43 threat-models split brain, impersonation and failover attacks.
- #46 adds the optional HA acceptance profile.
- #240 owns deployment packaging for distributed/heterogeneous profiles; HA remains optional.
