# Host resource pressure and admission

Issue #500 adds a portable resource-pressure layer beside the existing #14 capacity model.

The distinction is intentional:

- #14 answers whether a Node/Worker can satisfy a Job's declared capability and resource requirements;
- host-pressure admission answers whether an otherwise eligible Node/Worker is healthy enough **right now** to accept additional work safely.

Host-pressure admission never owns placement, reservations, dispatch, Task/Run lifecycle or retry state. The canonical `DeterministicScheduler` remains the only scheduling authority and consults pressure only after its ordinary eligibility filters and before reservation.

## Portable contract

`HostPressureSnapshot` contains only deployment-neutral state:

- overall `healthy`, `elevated`, `critical` or `unknown` state;
- zero or more portable `PressureSignal` dimensions such as CPU, memory, paging, storage, I/O, process/PID, descriptor, inode, throttling, exhaustion and headroom;
- optional portable measurements and units where the provider can supply a meaningful value;
- observation time;
- source/trust evidence;
- namespaced provider metadata through the existing `AdapterMetadata` boundary.

Linux paths, cgroup names, zRAM device names, hostnames and similar deployment details are not canonical identity or Job semantics.

## Admission actions

`PressureAdmissionPolicy` returns a deterministic `AdmissionDecision` with one of:

- `ADMIT`;
- `QUEUE`;
- `DENY_TEMPORARILY`;
- `BLOCK_FOR_MAINTENANCE`.

The first implementation keeps the policy conservative and explicit:

1. maintenance/draining blocks admission;
2. configured protected CPU/RAM/storage headroom is preserved before accepting additional work;
3. missing, untrusted or stale reports become `unknown` rather than being treated as healthy;
4. deployments may choose whether `unknown` is permissive or fail-closed with `require_pressure_report`;
5. `critical` pressure temporarily denies new work;
6. `elevated` pressure may queue configured advisory workload classes such as `heavy`, `exclusive` and `infrastructure-heavy` while still admitting lighter work;
7. `healthy` admits normally.

Protected headroom is deployment configuration, not a hard-coded machine/VPS SKU. It is intended for host/kernel, Control Plane, persistence, authentication/network edge and recovery/admin capacity.

## Scheduler integration

Pressure support is opt-in. Constructing `DeterministicScheduler` without a pressure policy preserves the existing #14 behavior.

When enabled, the scheduler:

1. runs the existing Node/Worker health, capability, runtime, trust, locality, capacity, accelerator and concurrency checks;
2. resolves the current pressure snapshot through the replaceable `PressureSnapshotProvider`;
3. evaluates `PressureAdmissionPolicy`;
4. rejects non-admitted candidates before any reservation is created;
5. continues to use the existing deterministic scoring/tie-break and reservation path for admitted candidates.

`pressure_admission()` exposes the structured decision for diagnostics/tests without reserving or dispatching work. The scheduler currently maps a non-admitted result conservatively onto the existing #14 rejection vocabulary while the precise action/reason remains available through `AdmissionDecision`. A later #500 slice can add richer Control Plane/telemetry projections without changing scheduling ownership.

## Freshness and trust

A pressure snapshot has an observation timestamp and trust flag. `max_snapshot_age` bounds how long it may influence admission. Stale or untrusted data is treated as `unknown`.

This is the portable seam needed for authenticated remote Worker reports: transport/authentication code determines whether received evidence is trusted; the admission policy does not authenticate transports itself.

Missing pressure support does not make a non-Linux or otherwise unsupported Worker unusable unless deployment policy explicitly sets `require_pressure_report=True`.

## Physical RAM, swap and zRAM

The canonical scheduler continues to satisfy `ram_min_bytes` only from the ordinary physical-memory resource report. Pressure admission does not add swap or zRAM capacity to `ResourceSnapshot.ram_available_bytes`.

A Linux provider may report paging, swap and zRAM diagnostics as pressure evidence, but must not model `physical RAM + swap/zRAM` as equivalent physical RAM.

## Follow-up #500 slices

This core intentionally precedes platform-specific providers and wider operational integration. Remaining issue-owned work includes:

- Linux PSI parsing/provider;
- swap/paging and major-fault evidence;
- zRAM metrics and effectiveness;
- cgroup v2 memory/PID/throttling/OOM evidence;
- disk/inode/descriptor collection;
- authenticated remote pressure report plumbing;
- #16 metrics/timeline integration and recovery transitions;
- Control Plane/doctor visibility;
- #39/#240 deployment hooks and operator guidance;
- #440 dedicated host-pressure benchmark profiles with hard safety bounds.

None of those follow-ups may silently tune kernel, swap, zRAM or cgroup settings; collection remains read-only by default.
