# Distributed runtime

Issue #14 introduces platform-owned Node, Worker, placement and remote-job semantics without making one host layout, GPU vendor, broker, container system or cloud provider canonical.

## Ownership boundary

The reference data flow is:

```text
Task / Run
    -> canonical ExecutionRequest
    -> WorkerJobRequest
    -> DeterministicScheduler
    -> capacity Reservation
    -> #15 dispatch authorization (when configured)
    -> WorkerDispatcher
    -> LifecycleBackend
    -> executor adapter
```

The scheduler does not own Task or Run lifecycle. It chooses an eligible Worker and reserves capacity for a canonical Worker Job. Execution still crosses the existing `LifecycleBackend` contract.

A one-machine installation uses this same path with one registered Node and `LocalWorker`. Adding remote workers therefore changes deployment topology, not canonical Task/Run semantics.

The persisted domain `Node` and `Worker` objects remain the canonical ownership/identity entities defined by the platform domain. `NodeRecord` and `WorkerRecord` are distributed-runtime state projections using those same canonical IDs; they do not introduce a second ownership model. Provider `NodeDescriptor` and `WorkerDescriptor` remain normalized discovery views.

## Distributed runtime records

`NodeRecord` describes a participating device using stable canonical `node_*` identity plus backend-neutral runtime facts:

- health/status, heartbeat evidence and canonical state-change timestamps;
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
- heartbeat, canonical state-change, drain and locality state.

Raw host paths, GPU vendor IDs, broker IDs and process/container IDs are not canonical identities.

## Registration and heartbeat

`RegistrationRequest` and `Heartbeat` are versioned through `WORKER_PROTOCOL_VERSION`.

The reference `DistributedRegistry` supports:

- initial registration;
- re-registration with the same canonical Node/Worker IDs;
- monotonic heartbeat sequence numbers;
- duplicate-heartbeat idempotency;
- independent Node and Worker liveness expiry;
- deterministic heartbeat expiry to offline state;
- drain/maintenance controls;
- graceful deregistration;
- reservation release on deregistration.

Both the outer registration/heartbeat protocol version and every declared `WorkerRecord.protocol_version` must match `WORKER_PROTOCOL_VERSION` before registry state is mutated. Explicit Worker deregistration also removes the corresponding ID from the owning Node's `worker_refs`, so discovery state cannot retain a stale schedulable Worker reference.

A Worker that disappears is not trusted as healthy indefinitely. Re-registration restores participation using the same canonical identity.

### Authenticated remote reporter

`WorkerProtocolService` is the worker-facing security boundary. It reuses #36 authentication and #15 authorization instead of inventing a second identity or permission system.

A remote registration identifies one reporter through `RegistrationRequest.service_identity_ref`. The reporter must:

- authenticate with a #36 Worker credential;
- match the credential-bound Worker identity;
- be present in the reported Worker snapshot;
- pass the credential's canonical `CredentialScope` ceiling;
- pass #15 authorization for the Node and every reported Worker.

Authenticated registration is treated as an authoritative Worker snapshot for that Node. A re-registration cannot silently omit known Workers; removal uses explicit deregistration. Authenticated heartbeats likewise report the complete registered Worker snapshot and cannot inject an unknown Worker.

Remote reports never control administrative trust state. For a new remotely enrolled Node, the reference service assigns `trust_level="untrusted"` unless the deployment chooses another explicit initial policy. Re-registration and heartbeat preserve Control-Plane-owned Node trust, maintenance and drain state as well as existing Worker drain state.

#36 replay protection and optional TLS peer binding are applied before the registry is mutated. The raw Worker credential is never persisted in distributed runtime state.

### State-change timestamp semantics

`last_heartbeat_at` is liveness evidence only. `updated_at` is the latest canonical caller-visible Node/Worker state change and is never assigned by Search or another derived consumer.

- registration/re-registration establishes both liveness and state-change time;
- a pure heartbeat refresh advances `last_heartbeat_at` but preserves `updated_at`;
- a heartbeat that changes canonical status/resources/Worker metadata advances `updated_at` as well;
- drain/maintenance changes advance `updated_at` without changing `last_heartbeat_at`;
- liveness expiry/offline transitions advance `updated_at` without rewriting the last accepted heartbeat;
- no-op administrative mutations preserve the previous state-change timestamp;
- state-change time is monotonic within the registry and all runtime timestamps are timezone-aware;
- restart health normalization to offline is a canonical state change when the persisted record was not already offline, but it is not a heartbeat.

Derived Search projections use canonical `updated_at`, so modification-time filters and ordering do not depend on heartbeat frequency.

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
- Worker concurrency.

Eligible candidates are scored only by explicit preferences such as preferred Worker/Node, labels and workspace/model/runtime locality. Equal scores use canonical Worker ID as a deterministic tie-break.

Every rejected candidate carries structured `RejectionReason` entries. The scheduler does not silently hide why a Worker was excluded.

When an upstream contract explicitly supplies a Worker ID, `schedule_to_worker(...)` evaluates exactly that Worker against the same hard filters and never falls back to another eligible Worker. Explicit placement therefore remains an identity constraint rather than being weakened into a scheduling preference.

## Capacity leases

Scheduling creates a `Reservation` before dispatch. Node CPU/RAM/storage capacity is accounted node-wide even when multiple Workers share the same host. Accelerator reservations bind a concrete accelerator ID and account VRAM so concurrent jobs cannot independently consume the same reported accelerator memory.

A newly selected reservation is `reserved`. Successful Worker acceptance commits it to `active`. Active leases are renewed by fresh Worker/reconciliation evidence and remain capacity-owning until terminal release or expiry. This prevents a long-running accepted job from losing its capacity claim merely because the initial dispatch lease elapsed.

The registry prevents a second Worker from claiming the same canonical Worker Job while its reservation is active and prevents subsequent jobs from overcommitting already-reserved capacity.

## Dispatch authorization

`DistributedRuntime` accepts the existing canonical #15 `AuthorizationProvider` as an optional deployment dependency. When configured, every new placement is authorized against the **exact selected `worker_*` resource before the external Worker dispatch boundary**.

The authorization request carries only canonical scope/context fields needed for policy evaluation: principal, action, Worker/Node, Project, Workspace, Task, Run and an unambiguous required capability when available. Job input and `secret_refs` are not copied into the policy request.

Placement still reserves capacity before this final authorization gate so the authorization decision refers to the deterministic selected Worker. A denial or authorization-provider failure releases that reservation before the Worker can start execution; no dispatch ownership record is created and the denied Worker Job is not executed.

Reference scheduler tests may construct a runtime without an authorization provider, matching the deterministic local/reference path allowed by #14. Production composition that requires #15 enforcement supplies the canonical provider rather than defining a distributed-specific policy system.

## Worker protocol and local reference worker

`WorkerJobRequest` is the transport-neutral job message. It carries:

- canonical execution request and Worker Job ID;
- placement requirements;
- workspace/snapshot references;
- input artifact references;
- secret references rather than secret values;
- actor/cancellation/timeout/idempotency/trace context.

`LocalWorker` implements the same `WorkerDispatcher` boundary that a remote transport adapter implements. It delegates execution to the existing `LifecycleBackend`, so ReferenceExecutor, Forge or another executor remain behind the established execution seam.

Duplicate delivery of the same exact `worker_job_id` is idempotent. Reusing that ID with a different payload is rejected.

## #35 replaceable Worker message transport

`TransportWorkerDispatcher` and `WorkerTransportEndpoint` adapt the transport-neutral Worker contract to the existing #35 `MessageTransport` abstraction. They do not introduce a second broker contract and do not select Redis, NATS, Kafka, RabbitMQ or another permanent transport.

Worker operations are carried as versioned command/reply envelopes for:

- `dispatch`;
- `get`;
- `cancel`;
- terminal `result` retrieval including canonical artifact and evidence references.

The identity layers remain distinct:

- `worker_job_id` is the canonical Worker execution/idempotency identity;
- transport `message_id` is only one delivery identity;
- correlation/causation, Task, Run, project and idempotency metadata are propagated through the #35 envelope.

#35 is explicitly at-least-once. The distributed runtime therefore does not claim exactly-once messaging. A lost dispatch reply can be retried with a new transport message ID and the same Worker Job identity; the Worker executes the canonical Run only once. Likewise, a lost terminal result reply is recovered by repeating result retrieval, not by redispatching the execution. Tests prove that Artifact/Evidence references survive that retry while the lifecycle start count remains unchanged.

Only portable secret references may appear in Worker job envelopes. Plaintext secret material is resolved at the execution boundary and is not copied into transport payloads, persistence, telemetry or Control Plane projections.

## Failure and reconciliation

`DistributedRuntime` records dispatch ownership and reconciles active work.

Current reference behavior:

- a Worker that is offline or unreachable moves active dispatch state to `lost`;
- a later Worker rejoin is reconciled against the Worker's execution state instead of guessing completion;
- cancellation requested while a Worker is unreachable becomes `cancel_pending`;
- once the Worker is reachable again the pending cancellation is applied;
- terminal execution releases the active capacity reservation;
- a lost acknowledgement preserves dispatch ownership and capacity until reconciliation instead of making unsafe parallel redispatch possible;
- Worker dispatch remains idempotent by Worker Job ID;
- lost transport replies are retried against the same Worker Job identity rather than interpreted as proof that execution did not happen.

This deliberately avoids both an unsafe "still running forever" assumption and an unsafe immediate duplicate dispatch after loss of an acknowledgement.

## Controlled cross-Worker failover

Liveness loss is not proof that the previous execution stopped. A network partition, missed heartbeat or unreachable dispatcher therefore moves the Worker Job to `lost` but does **not** by itself authorize a second Worker to run the same canonical work.

Controlled ownership transfer uses a separate, replaceable `WorkerOwnershipFencer`. A deployment-specific supervisor, process manager or other trusted authority may implement that protocol and return a `FailoverFenceReceipt` proving that the exact `(worker_job_id, worker_id)` ownership has been fenced/stopped. No concrete supervisor or infrastructure product is canonical.

The safe state transition is:

```text
running/dispatched
    -> lost
    -> valid matching fence receipt
    -> fenced
    -> new reservation on a different eligible Worker
    -> next dispatch attempt
```

The rules are:

- `RetryMode.NEVER` is never eligible for cross-Worker failover;
- retry-safe `SAFE`/`IDEMPOTENT` work may transfer only after successful fencing;
- a missing, failed or identity-mismatched fence leaves the Worker Job `lost` and preserves its old capacity claim;
- the old reservation is released only after the valid fence has been accepted;
- `fenced` is persisted before a replacement dispatch, so a Control-Plane restart between fencing and redispatch cannot recreate two valid owners;
- the replacement Worker is evaluated through the same scheduler hard filters and deterministic scoring used for ordinary placement;
- the previous Worker is explicitly excluded from the replacement candidate set;
- `worker_job_id`, `ExecutionRequest`/Run identity, requirements, workspace/snapshot/artifact/secret references, actor/cancellation/idempotency and trace context remain unchanged;
- only `dispatch_attempt` advances for the new ownership attempt;
- cancellation while `fenced` terminates the canonical Worker Job instead of starting a replacement;
- if no alternate attached Worker is eligible, the job remains safely `fenced` without an active capacity claim;
- a late return of the previously fenced Worker cannot reclaim the current dispatch record after ownership moved to the replacement Worker.

Tests cover network partition without fencing, retry-forbidden work, invalid fence identity, A-to-B capacity transfer, deterministic alternate selection, restart between fence and redispatch, cancellation while fenced, no-replacement behavior and late old-Worker rejoin.

## Restart persistence

`DistributedStateStore` is a replaceable persistence boundary. `JsonDistributedStateStore` is the dependency-free reference implementation and atomically replaces one versioned JSON snapshot containing:

- Node runtime records;
- Worker runtime records;
- heartbeat sequence state;
- active/reserved capacity claims;
- dispatch ownership records and portable Worker Job data;
- retrieved terminal `WorkerJobResult` state including canonical artifact/evidence references.

Distributed JSON state schema v3 adds explicit Node/Worker `updated_at` state-change timestamps. The reference store remains able to restore schema v1/v2 snapshots; missing state-change timestamps are derived conservatively from the later of registration and heartbeat time before restart health normalization. Schema v2 added the optional terminal result field, so a v1 dispatch record still restores with no cached result and can recover the result from the same Worker after reachability is re-established.

The runtime persists dispatch ownership before the external Worker acknowledgement can be lost. After Control-Plane restart, persisted health is deliberately restored as offline rather than trusted as fresh liveness evidence. Re-registration/heartbeat then re-establishes reachability and reconciliation continues without duplicating execution.

Terminal execution state and result collection are intentionally separate. `DistributedRuntime.result(...)` first returns a previously persisted `WorkerJobResult`; otherwise it queries the exact owning result-capable Worker, validates Worker Job/Worker/Run identity and persists the canonical result. A lost completion response therefore survives Control-Plane restart: the same Worker Job result is requested again after rejoin, without a new dispatch. Once persisted, the result remains retrievable after another restart even when no Worker is attached.

A persisted `fenced` dispatch record is intentionally restored without an old active reservation. The replacement attempt may then be scheduled after eligible Worker liveness is re-established; fencing is not repeated merely because the Control Plane restarted.

The JSON store is a reference backend, not a canonical database choice. A durable database implementation can replace it without changing scheduling semantics.

## Control Plane integration

`register_distributed_control_plane(...)` extends the existing generic Control Plane registration seam rather than creating a separate API stack.

Registered read collections are:

- `nodes`;
- `workers`;
- `worker-jobs`.

Administrative commands currently include Node drain/undrain, Node maintenance enable/disable and Worker drain/undrain. They inherit the existing Control Plane idempotency and #15 authorization boundary.

Worker-job projections intentionally omit `secret_refs`. Input `artifact_refs` remain distinct from the optional terminal `result` projection. When a terminal result has been collected, the read-only projection exposes its status, canonical output artifact refs, evidence refs, error category, completion time and execution status; it never exposes secret references or plaintext secret material.

Remote Worker registration and heartbeat are not implemented as ordinary human/admin Control Plane commands. They use `WorkerProtocolService`, because they require #36 Worker-token authentication, replay protection, reporter binding and protocol-specific authorization before any runtime mutation.

## #5 provider contract integration

`DistributedNodeProvider` and `DistributedWorkerProvider` adapt the same distributed runtime to the existing replaceable `NodeProvider` and `WorkerProvider` contracts from #5. They do not create a second registry or scheduler.

`NodeDescriptor` and `WorkerDescriptor` remain discovery views. Registration preserves canonical Node/Worker IDs and passes the current complete sibling Worker snapshot back through the runtime so a provider-level upsert cannot accidentally mark unrelated Workers offline.

`DistributedWorkerProvider.dispatch(worker_id, request)` uses the exact-worker dispatch path. It either executes on the requested eligible Worker or returns a canonical `ContractError`; it never silently reroutes work to another Worker. The adapter derives a deterministic canonical Worker Job ID from `(worker_id, run_id)`, so an identical repeated provider dispatch reuses the same runtime record while a changed payload for the same Worker/Run identity is rejected as a conflict.

Registry/scheduler failures are translated to backend-neutral `ContractError` categories before crossing the provider boundary. Adapter-private metadata remains namespaced and is not exposed through the public Control Plane resource projections.

## #16 observability integration

`DistributedTelemetry` connects #14-owned scheduler, Worker, Node, reservation and reconciliation semantics to the existing #16 `Telemetry` facade. It does not own an exporter, storage backend or second tracing system.

The integration emits structured, correlated data for:

- scheduling candidates, acceptance scores and rejection reason codes;
- selected scheduling decisions;
- reservation creation, commit, renewal/release and reserved CPU/RAM/storage/VRAM;
- Worker dispatch duration and dispatch failures;
- Node/Worker heartbeat events and heartbeat age;
- Node resource availability and Worker active-job/concurrency state;
- reconciliation state transitions and liveness failure codes.

Telemetry context preserves canonical Task/Step, Run, Worker Job, Node, Worker, correlation and causation references when available. Job input payloads and `secret_refs` are deliberately not copied into metrics, logs, timeline entries or spans. Tests include explicit private input/secret markers and assert that those markers never reach the in-memory telemetry exporter.

## #37 remote workspace integration

The distributed runtime consumes the existing #37 remote materialization contracts instead of defining a second workspace-transfer protocol.

`WorkspaceJobMaterializationResolver` resolves the canonical `workspace_ref` and `snapshot_ref` carried by `WorkerJobRequest` through the existing `WorkspaceProvider`, verifies that the snapshot belongs to the workspace, and creates the existing `RemoteMaterializationRequest` with the canonical snapshot checksum and workspace access mode. The persisted Worker Job continues to carry only canonical references; host-local execution paths never become part of the distributed contract.

`MaterializingWorkerDispatcher` composes a `RemoteWorkspaceMaterializer` around an ordinary `WorkerDispatcher`:

1. resolve the canonical workspace/snapshot references;
2. materialize the snapshot on the exact Worker before execution;
3. validate receipt Worker/workspace/snapshot/checksum/access-mode identity;
4. dispatch the unchanged canonical Worker Job;
5. collect canonical result/artifact evidence only after terminal execution;
6. clean up using the #37 acknowledgement/outcome contract.

A lost dispatch acknowledgement does not trigger premature cleanup or a second workspace materialization. The wrapper retains the original materialization receipt and reuses it when the same idempotent Worker Job is retried/reconciled. Remote result artifact IDs are folded into `WorkerJobResult` without exposing a host path.

## #34 scoped secret delivery

`SecretDeliveringWorkerDispatcher` resolves canonical `SecretReference` objects only at the exact Worker execution boundary. Resolution uses the existing #34 `SecretProvider` / `SecretAccessContext` contracts and therefore composes with the established #15 authorization boundary rather than creating a second distributed secret system.

`WorkerJobRequest` stores only opaque secret references. Plaintext `SecretMaterial` exists only in the ephemeral per-dispatch bundle passed to a secret-aware execution adapter and is not stored in distributed JSON persistence, Worker Job Control Plane resources, #16 telemetry or #35 transport envelopes.

## Issue #14 completion status

The distributed-runtime scope now includes canonical runtime projections, versioned registration/heartbeat, capability/resource scheduling, node-wide and accelerator capacity leases, local and remote Worker dispatch, authenticated Worker reporting, explicit #15 pre-dispatch authorization hooks, provider/Control-Plane integration, restart persistence, durable terminal result/evidence recovery, remote workspace composition, scoped secret delivery, structured telemetry, replaceable #35 transport, lost-reply recovery and controlled fenced cross-Worker failover.

The acceptance path uses the same canonical `ExecutionRequest`/`WorkerJobRequest` regardless of whether the selected Worker is local or remote. Tests cover ordinary single-/multi-Worker scheduling as well as controlled transfer of the same canonical work from one lost/fenced Worker to another eligible Worker without changing Task or agent logic. Result recovery tests additionally prove lost-completion-response -> Control-Plane restart -> same-Worker result recovery -> second restart without Worker attachment, while the original execution is started only once.

No infrastructure-provider identifier, host name, GPU vendor, broker, container runtime or deployment topology becomes a canonical execution identity. Deployment profiles such as #240 consume these contracts rather than introducing a second scheduler.

No additional functional #14 subsystem is intentionally deferred. Issue closure requires the final synchronized CI/integration review of the completing PR.
