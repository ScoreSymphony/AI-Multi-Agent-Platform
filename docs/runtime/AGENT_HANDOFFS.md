# Canonical Agent Handoffs

Issue #592 adds a small platform-owned contract for explicit work transfer between canonical
Agents and Agent Teams. A Handoff records what a producer intentionally passes to the next
work boundary. It does **not** become a Task, Step, Run, conversation, message bus or workflow
lifecycle.

## Ownership boundary

Canonical execution ownership remains unchanged:

```text
Task -> Plan -> Step -> Run
          |
          +-- dependency/progression authority: durable Plan/Step coordinator

Agent/AgentTeam revision
          |
          +-- identity/execution authority: Agent runtime

Artifact / Result / Research / Skill / Context resources
          |
          +-- source authority: their owning services

AgentHandoff
          |
          +-- immutable semantic transfer evidence only
```

A Handoff therefore references authoritative resources rather than copying large payloads into
a second truth store. It cannot grant capabilities, permissions, secrets, Task scope or Step
assignment.

## Canonical contract

`HandoffContent` records:

- Task, Plan, producer Step, consumer Step and producer Run references;
- exact producer Agent or Agent Team revision;
- exact intended consumer revision, or provider-neutral consumer requirements;
- objective and completed-work summary;
- unresolved questions and blockers;
- assumptions and constraints that the consumer must preserve;
- exact Artifact/Result references and optional Research/Skill/Context references;
- recommended next action and requested output;
- canonical provenance.

`AgentHandoff` adds a stable `handoff_id`, monotonically increasing revision, creation time,
schema version and deterministic SHA-256 content digest. Historical revisions are immutable.
Large content remains in Files/Artifacts or another owning source domain.

## Creation semantics

`HandoffService.create_handoff(...)` is explicit and durable. Before persistence it verifies:

1. the exact producer Agent/Team revision exists;
2. an exact intended consumer revision exists when one is pinned;
3. every referenced source exists;
4. the producer is allowed to read every referenced source;
5. a pinned consumer is already allowed to read every referenced source.

Creation uses an idempotency key. Reusing the same key with the same semantic content returns
the existing Handoff. Reusing it with different content fails with a canonical conflict.
Revision creation uses optimistic `expected_previous_revision` semantics so a stale producer
cannot silently replace a newer revision.

The service does not advance Steps or create Runs. Those operations remain owned by the
canonical planning/coordinator path.

## Durable Plan/Step coordination binding

`CoordinatedHandoffService` is the read-only integration seam with the durable #384 coordinator.
Production paths that transfer planned work use this wrapper around `HandoffService` so the
semantic Handoff cannot drift away from canonical execution identity.

Before creation it proves that:

- the referenced Plan belongs to the referenced Task;
- producer and consumer Steps exist in that canonical Plan;
- the producer Step coordination record belongs to the same Task/Plan;
- `producer_run_id` is the producer Step's canonical latest Run.

Before consumption it proves that the consumer Step still belongs to the same Task/Plan and
that the supplied consuming Run is the consumer Step's canonical latest Run. A mismatch fails
before any Handoff-to-Run consumption binding is persisted.

The wrapper only calls coordinator read methods. It cannot activate Plans, progress Steps,
create Runs, assign Agents, retry work or repair coordinator state, so #384/#439 remain the
sole lifecycle/progression authority.

## Consumption semantics

`HandoffService.consume_handoff(...)` requires an exact consuming Run ID and exact consumer
Agent/Team revision. Before returning runtime context it:

1. confirms the exact consumer revision still exists;
2. confirms that the consumer is the pinned recipient or satisfies an injected canonical
   consumer-requirement policy;
3. revalidates every source reference for existence and read authorization;
4. optionally validates an independently supplied exact ContextBundle reference;
5. durably records `handoff_id + revision + digest -> consuming Run`;
6. only then returns the Handoff as runtime context.

This ordering prevents an Agent from relying on transfer content that was never durably bound
to the consuming Run. Repeated consumption of the same semantic binding is idempotent even
when the retry happens later and therefore has a different attempted timestamp.

Missing or stale references fail explicitly. Authorization is rechecked at consumption time;
a source that became unreadable after Handoff creation is not silently dereferenced.

## Handoff versus Context Bundle

The #590 integration keeps the two lifecycles separate while making the Handoff an actual
canonical context source:

- `AgentHandoff` remains the semantic work-transfer artifact and canonical source authority.
- `HandoffContextSource` exposes the exact Handoff ID, revision, digest and execution references.
- `ContextSourceType.AGENT_HANDOFF` gives #590 a stable source vocabulary without introducing a
  Handoff-specific resolver or permission system.
- `ConsumedHandoffContextAdapter` accepts only `HandoffRuntimeContext` values whose exact
  consuming-Run binding is already durable, then contributes them through the normal #590
  `ContextSourceAdapter` seam.
- The adapter snapshots the compact structured Handoff itself as deterministic inline context,
  so `ReferenceContextRenderer` and context-bound Agent execution can consume it without
  orchestrator-private state or a special content provider.
- The Context source identity retains the canonical Handoff digest; the Context entry's own
  content digest protects the rendered snapshot. These are deliberately separate identities.
- Producer statements enter the Context Bundle with `UNTRUSTED` trust and `CONTEXT` role, so a
  Handoff cannot acquire security/instruction authority merely by being included.
- Referenced Artifacts, Results and Research Evidence are not copied into the Context Bundle by
  this bridge. Their owning domains and normal authorization/verification boundaries remain
  authoritative.

The #590 resolver applies its normal read-authorization gate before including the Handoff.
A denied mandatory Handoff fails closed. Collection is additionally scoped to the exact Task,
consuming Run, Plan/consumer Step and, for direct Agent consumers, exact Agent revision.

`HandoffConsumption.context_bundle_ref` remains available when an exact Context Bundle is
already known at the consumption boundary. It is not a second ownership mechanism: the final
#590 `ContextRunBinding` remains the canonical proof of which complete Context Bundle an
AgentRun actually used, while Handoff consumption independently proves which exact Handoff
revision was bound to that Run.

## Persistence and recovery

`HandoffRepository` is a narrow replaceable seam. The reference implementations are:

- `InMemoryHandoffRepository` for deterministic unit/contract tests;
- `SQLiteHandoffRepository` for durable local/single-node operation without an external broker
  or paid service.

SQLite persistence stores immutable Handoff revisions, idempotency records and exact consuming
Run bindings. Re-opening the repository after process restart reconstructs the canonical
Handoff through the versioned codec and preserves consumption evidence.

## Authorization and source dereferencing

`HandoffReferenceGateway` is intentionally a narrow enforcement seam rather than a second
permission system. Production composition must implement it by delegating to the source-owning
Artifact/Result/Research/Skill/Context service plus canonical #15 authorization.

A Handoff never treats visibility as permission and never transports secret values. Source
references are identifiers/revisions/digests only. ContextBundle inclusion adds a separate
normal #590 read-authorization gate for the Handoff source itself; it never replaces or weakens
source-resource authorization.

## Observability and Control Plane

Creation, consumption and denied source dereferencing expose structured `HandoffAuditEvent`
evidence. `HandoffControlPlaneProjection` provides a permission-aware read-only projection for
Task/Step history including:

- producer and intended consumer revision;
- objective/completed work;
- unresolved questions/blockers;
- referenced source identities/digests;
- Handoff digest/revision;
- consuming Run IDs.

The projection has no mutation command and therefore cannot become a parallel workflow or
assignment authority.

## Orchestrator replacement

No canonical Handoff field contains an orchestrator/provider-private runtime identity. An
orchestrator may render or transport the Handoff, but replacing the orchestrator leaves the
canonical Handoff ID, revision, digest, producer/consumer revisions and consuming Run binding
unchanged. ContextBundle rendering receives the same platform-owned Handoff snapshot regardless
of the selected orchestrator adapter.

## Security invariants

- Handoffs do not grant permissions or capabilities.
- Referenced data must pass normal source-resource authorization.
- ContextBundle inclusion is separately authorization-gated and fail-closed when mandatory.
- Producer Handoff statements remain untrusted context rather than verification authority.
- Missing/stale references fail explicitly.
- Historical Handoff revisions are immutable.
- Exact Agent/Team revisions are pinned.
- No private chain-of-thought is stored.
- No external message broker or paid service is required for the reference path.
