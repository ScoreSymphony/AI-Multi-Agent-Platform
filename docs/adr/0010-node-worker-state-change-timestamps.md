# ADR 0010 — Separate Node/Worker state-change time from heartbeat evidence

- **Status:** Accepted
- **Issue:** #414
- **Date:** 2026-09-06

## Context

The distributed runtime already records `registered_at` and `last_heartbeat_at` for canonical Node and Worker runtime projections. Derived consumers such as global Search also need a modification timestamp for filtering and ordering. Mapping `last_heartbeat_at` to a generic `updated_at` is incorrect because maintenance, draining, liveness expiry, restart health normalization and other canonical state transitions can happen without a heartbeat.

Conversely, advancing a generic modification timestamp for every otherwise unchanged heartbeat would simply rename liveness evidence and make high-frequency heartbeat traffic appear as meaningful state mutation.

## Decision

`NodeRecord` and `WorkerRecord` expose a timezone-aware canonical `updated_at` that is independent from `last_heartbeat_at`.

`last_heartbeat_at` means only the latest accepted liveness report. `updated_at` means the latest canonical caller-visible state change. A pure heartbeat refresh advances `last_heartbeat_at` only. A heartbeat that changes canonical health, resources, capacity or Worker metadata advances both timestamps. Registration and re-registration establish a new state timestamp. Administrative drain/maintenance changes and liveness-expiry transitions advance `updated_at` without fabricating a heartbeat. No-op administrative mutations do not advance it.

State timestamps advance monotonically within the registry with `max(previous_updated_at, event_time)`, so delayed or regressed event clocks cannot move modification history backwards. All canonical runtime timestamps must be timezone-aware.

Worker heartbeat payload timestamps are not trusted as Control-Plane modification truth. Existing Worker `registered_at` and canonical `updated_at` are preserved unless the accepted heartbeat actually changes canonical Worker state; a Worker first introduced through the generic registry heartbeat path receives the accepted heartbeat observation time.

On Control-Plane restart, persisted health is normalized to offline because persisted health is not fresh liveness evidence. When that normalization changes visible state, restore time advances `updated_at` while preserving `last_heartbeat_at`. Already-offline restored records retain their prior `updated_at`.

Distributed-state schema v3 persists the new field. The reference decoder remains compatible with v1/v2 snapshots by deriving missing `updated_at` as the later of `registered_at` and `last_heartbeat_at` before conservative restart normalization.

Global Search and other derived consumers use canonical `updated_at`; they never become timestamp authorities themselves.

## Consequences

- Heartbeat age remains semantically reliable liveness evidence.
- Search `updated_after`, `updated_before` and `sort=updated_at` reflect state mutation instead of heartbeat frequency.
- Operator and observability views can show heartbeat and modification time independently.
- Administrative state changes become discoverable by modification time without pretending a Worker reported them.
- Persistence schema v3 adds explicit modification timestamps while preserving restore compatibility for v1/v2.
- Scheduler eligibility and heartbeat timeout behavior are unchanged apart from timestamp bookkeeping.

## Alternatives considered

### Continue using `last_heartbeat_at` as `updated_at`

Rejected because non-heartbeat state changes would carry stale modification times and heartbeat traffic would be misrepresented as generic mutation.

### Advance `updated_at` for every heartbeat

Rejected because this would preserve the semantic conflation under a second field.

### Let Search or observability assign modification time

Rejected because derived consumers are not canonical state authorities and could disagree with one another.

### Use provider/backend timestamps

Rejected because it would violate deployment neutrality and make canonical ordering depend on replaceable infrastructure.

## Affected issues and contracts

- #414 owns this timestamp semantic.
- #14 supplies canonical Node/Worker registry and liveness behavior.
- #288 consumes `updated_at` for Node/Worker Search projections.
- #16 may consume heartbeat and state-change times independently for observability.
