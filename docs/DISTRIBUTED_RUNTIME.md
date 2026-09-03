# Distributed runtime baseline

Issue #14 introduces platform-owned Node, Worker, placement and remote-job semantics without making one host layout, GPU vendor, broker, container system or cloud provider canonical.

## Ownership boundary

The reference data flow is:

```text
Task / Run
    -> canonical ExecutionRequest
    -> WorkerJobRequest
    -> DeterministicScheduler
    -> capacity Reservation
    -> WorkerDispatcher
    -> LifecycleBackend
    -> executor adapter
```

The scheduler does not own Task or Run lifecycle. It chooses an eligible Worker and reserves capacity for a canonical Worker Job. Execution still crosses the existing `LifecycleBackend` contract.

A one-machine installation uses this same path with one registered Node and `LocalWorker`. Adding remote workers therefore changes deployment topology, not canonical Task/Run semantics.

## Canonical runtime records

`NodeRecord` describes a participating device using stable canonical `node_*` identity plus backend-neutral runtime facts:

- health/status and heartbeat timestamps;
- CPU, RAM and storage capacity;
- generic accelerator inventory and available memory;
- operating-system/platform/architecture metadata;
- runtimes, models and capabilities currently available;
- labels, trust classification and locality hints;
- drain/maintenance/network state;
- namespaced adapter metadata.

`WorkerRecord` describes a schedulable process/service attached to a Node:

- canonical `worker_*` identity;
- worker type;
- supported executors, runtimes, models and capabilities;
- concurrency limit and active-job count;
- health/status;
- protocol and implementation version;
- heartbeat/drain/locality state.

Raw host paths, GPU vendor IDs, broker IDs and process/container IDs are not canonical identities.

## Registration and heartbeat

`RegistrationRequest` and `Heartbeat` are versioned through `WORKER_PROTOCOL_VERSION`.

The reference `DistributedRegistry` supports:

- initial registration;
- re-registration with the same canonical Node/Worker IDs;
- monotonic heartbeat sequence numbers;
- duplicate-heartbeat idempotency;
- deterministic heartbeat expiry to offline state;
- drain/maintenance controls;
- graceful deregistration;
- reservation release on deregistration.

A worker that disappears is not trusted as healthy indefinitely. Re-registration restores participation using the same canonical identity.

## Placement policy

`JobRequirements` separates hard requirements from preferences.

Hard filters currently cover:

- Node/Worker health and drain state;
- executor type;
- capabilities;
- runtime and OS constraints;
- CPU, RAM and storage capacity;
- accelerator/VRAM requirements;
- model availability;
- trust level;
- required labels;
- anti-affinity exclusions;
- network requirement;
- worker concurrency.

Eligible candidates are scored only by explicit preferences such as preferred Worker/Node, labels and workspace/model/runtime locality. Equal scores use canonical Worker ID as a deterministic tie-break.

Every rejected candidate carries structured `RejectionReason` entries. The scheduler does not silently hide why a Worker was excluded.

## Capacity leases

Scheduling creates a `Reservation` before dispatch. The reference lease tracks CPU, RAM, storage and concurrency claims and has a bounded expiry.

The registry prevents a second Worker from claiming the same canonical Worker Job while its reservation is active, and it prevents subsequent jobs from overcommitting the already-reserved capacity.

The reservation layer is intentionally independent from a future persistent database implementation. A persistent registry can implement the same semantics without changing the scheduler contract.

## Worker protocol and local reference worker

`WorkerJobRequest` is the transport-neutral job message. It carries:

- canonical execution request and Worker Job ID;
- placement requirements;
- workspace/snapshot references;
- input artifact references;
- secret references rather than secret values;
- actor/cancellation/timeout/idempotency/trace context.

`LocalWorker` implements the same `WorkerDispatcher` boundary that a future remote transport adapter uses. It delegates execution to the existing `LifecycleBackend`, so ReferenceExecutor, Forge or another executor remain behind the established execution seam.

Duplicate delivery of the same exact `worker_job_id` is idempotent. Reusing that ID with a different payload is rejected.

## Failure and reconciliation baseline

`DistributedRuntime` records dispatch ownership and reconciles active work.

Current reference behavior:

- a Worker that is offline or unreachable moves active dispatch state to `lost`;
- a later Worker rejoin is reconciled against the Worker's execution state instead of guessing completion;
- cancellation requested while a Worker is unreachable becomes `cancel_pending`;
- once the Worker is reachable again the pending cancellation is applied;
- terminal execution releases the active capacity reservation;
- a lost acknowledgement cannot cause duplicate local execution because Worker dispatch is idempotent by Worker Job ID.

This deliberately avoids an unsafe "still running forever" assumption after loss of liveness.

## Security integration points

This baseline carries security references but does not invent a second authentication/authorization system.

Follow-up composition for #14 must use the existing platform security boundaries for:

- authenticated Node/Worker enrollment and request identity;
- authorization of register, heartbeat, dispatch, drain and deregister actions;
- trust-level admission;
- scoped SecretReference delivery;
- replay resistance and transport security for remote adapters.

No plaintext secret belongs in Node/Worker registration, heartbeat or ordinary scheduling diagnostics.

## Workspace integration

Issue #37 already defines remote workspace materialization requests, receipts, results and cleanup acknowledgements. Remote Worker adapters should consume those contracts rather than transfer host-local paths.

The distributed job contract therefore carries canonical workspace/snapshot references only. Actual remote materialization and artifact return remain a composition step over the #37 contracts.

## Remaining #14 integration work

The current baseline establishes contracts, in-memory reference registry, deterministic scheduling, leases, local Worker dispatch and reconciliation. Full issue completion still requires composition work including:

- Control Plane Node/Worker/job APIs;
- #15/#36 authorization and Worker authentication enforcement at those APIs;
- scoped secret delivery integration;
- concrete remote workspace materialization/return flow;
- distributed trace/resource telemetry hooks;
- persistence/restart reconciliation beyond the in-memory reference registry;
- a real remote transport fixture while keeping the transport replaceable;
- the remaining acceptance/security/recovery tests from #14.
