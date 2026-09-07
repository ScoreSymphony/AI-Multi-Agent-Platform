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

One pressure snapshot is sampled per Node for one scheduler evaluation and shared by otherwise-eligible Workers on that Node. This keeps delta-based providers such as paging/throttling collectors deterministic across candidates without turning the cache into durable scheduler state. A later evaluation samples again.

`pressure_admission()` exposes the structured decision for diagnostics/tests without reserving or dispatching work. Diagnostic calls do not count as scheduler admission telemetry. The scheduler maps a non-admitted result conservatively onto the existing #14 rejection vocabulary while the precise action/reason remains available through `AdmissionDecision`.

## #16 observability integration

Pressure telemetry is emitted through the existing #16 `Telemetry` facade; issue #500 does not create a second metrics, timeline or exporter system.

For actual pressure-aware scheduler candidate evaluations the integration exposes:

- normalized Node pressure observations and portable pressure-signal dimensions;
- portable signal values/units where present;
- pressure-aware scheduler admission actions and structured reason codes;
- pressure snapshot age used by admission;
- correlated scheduler pressure-admission timeline entries;
- Node pressure transitions and explicit recovery from `elevated`/`critical` to `healthy`.

The integration deliberately excludes `HostPressureSnapshot.source_ref` and `provider_metadata` from canonical telemetry. Linux proc/sys/cgroup paths, zRAM device names and other provider-private evidence therefore remain behind their adapter namespace instead of leaking into ordinary #16 records.

Repeated use of the same Node observation is de-duplicated for Node-level observation/signal telemetry while admission decisions remain per candidate. This preserves explainability for multi-Worker Nodes without multiplying identical host measurements.

## Freshness and trust

A pressure snapshot has an observation timestamp and trust flag. `max_snapshot_age` bounds how long it may influence admission. Stale or untrusted data is treated as `unknown`.

Missing pressure support does not make a non-Linux or otherwise unsupported Worker unusable unless deployment policy explicitly sets `require_pressure_report=True`.

## Authenticated remote pressure reporting

Remote Workers reuse the existing authenticated #14 Worker protocol and its `WorkerRecord.adapter_metadata` transport. No second pressure transport or credential model is introduced.

A reporting Worker may attach `platform.host-pressure-report.v1` metadata containing only the portable pressure state, observation timestamp and normalized signals. `pressure_report_metadata()` intentionally omits provider-private source references, Linux metadata and any Worker-supplied trust assertion. `attach_pressure_report()` also removes stale report/provenance copies before attaching the current portable report.

`DistributedWorkerProcess` accepts a replaceable `PressureSnapshotProvider`. The reporting Worker samples it for registration and heartbeat snapshots and attaches the portable report only to the authenticated reporter Worker record. The shipped Linux Worker CLI composes `LinuxHostPressureProvider` automatically on Linux; the collector remains read-only.

Trust is assigned only after the existing Worker authentication/authorization boundary succeeds. `WorkerProtocolService`:

1. binds the request to the authenticated `service_identity_ref`;
2. removes any remotely supplied `platform.host-pressure-provenance.v1` claim;
3. removes pressure reports from non-reporter sibling Worker records;
4. adds service-owned provenance only to the authenticated reporter when a portable report is present.

`RegistryPressureSnapshotProvider` resolves only report/provenance pairs that match Node ID, reporter Worker ID and the `worker_protocol` authentication marker. A remote report timestamp more than five seconds ahead of the Control Plane acceptance time is rejected instead of being treated as indefinitely fresh. Older reports remain valid evidence but are still subject to the normal `PressureAdmissionPolicy.max_snapshot_age` freshness bound.

The HTTP Worker codec already serializes Worker `adapter_metadata`, so these reports cross the existing TLS/authenticated Worker protocol without changing its wire identity or adding credentials to canonical payloads.

## Physical RAM, swap and zRAM

The canonical scheduler continues to satisfy `ram_min_bytes` only from the ordinary physical-memory resource report. Pressure admission does not add swap or zRAM capacity to `ResourceSnapshot.ram_available_bytes`.

A Linux provider may report paging, swap and zRAM diagnostics as pressure evidence, but must not model `physical RAM + swap/zRAM` as equivalent physical RAM.

## Optional Linux provider

`LinuxHostPressureProvider` is a read-only adapter behind the portable pressure contract. It accepts injected procfs, sysfs, cgroup-v2 and storage roots so tests and constrained deployments can expose only the evidence they support. Missing files or unsupported Linux facilities produce incomplete or `unknown` evidence instead of mutating the host or failing the scheduler.

The provider currently normalizes:

- Linux PSI CPU, memory and I/O stall evidence;
- swap utilization, paging deltas and major-fault rates;
- zRAM device size, utilization, compression effectiveness and configured swap priority as namespaced diagnostics;
- cgroup-v2 memory limits/events, PID pressure, CPU throttling and new OOM/OOM-kill events;
- filesystem free-space and inode pressure;
- host file-descriptor utilization.

Linux-specific paths, counters and device details remain under the `linux.host_pressure` adapter metadata namespace. Only normalized `PressureSignal` values cross into scheduler policy. Collection is read-only and does not tune swap, zRAM, cgroups, kernel parameters or filesystem limits.

Provider thresholds are deployment-overridable normalization inputs, not canonical hardware requirements. Counter-based signals use deltas between observations; the first observation therefore has no fabricated rate.

## Follow-up #500 slices

The portable core, Linux collector, #16 projection and authenticated remote reporting intentionally precede wider operational integration. Remaining issue-owned work includes:

- Control Plane/doctor visibility;
- additional #39/#240 operator configuration and guidance;
- #440 dedicated host-pressure benchmark profiles with hard safety bounds.

None of those follow-ups may silently tune kernel, swap, zRAM or cgroup settings; collection remains read-only by default.
