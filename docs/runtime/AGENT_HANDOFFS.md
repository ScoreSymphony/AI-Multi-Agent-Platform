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

Artifact / Result / Research / future Skill/Context resources
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

## Consumption semantics

`HandoffService.consume_handoff(...)` requires an exact consuming Run ID and exact consumer
Agent/Team revision. Before returning runtime context it:

1. confirms the exact consumer revision still exists;
2. confirms that the consumer is the pinned recipient or satisfies an injected canonical
   consumer-requirement policy;
3. revalidates every source reference for existence and read authorization;
4. optionally validates a future ContextBundle reference;
5. durably records `handoff_id + revision + digest -> consuming Run`;
6. only then returns the Handoff as runtime context.

This ordering prevents an Agent from relying on transfer content that was never durably bound
to the consuming Run. Repeated consumption of the same semantic binding is idempotent even
when the retry happens later and therefore has a different attempted timestamp.

Missing or stale references fail explicitly. Authorization is rechecked at consumption time;
a source that became unreadable after Handoff creation is not silently dereferenced.

## Handoff versus Context Bundle

The types intentionally keep the #590 integration narrow:

- `AgentHandoff` is the semantic work-transfer artifact.
- `HandoffContextSource` exposes the exact Handoff ID/revision/digest as one provider-neutral
  source candidate for a future canonical `ContextBundle`.
- `HandoffConsumption.context_bundle_ref` can record the exact Context Bundle that delivered
  the Handoff once #590 is implemented.

This does not implement or shadow the Context Bundle resolver. The same pattern applies to the
progressive Skill Bundle (#588) and Research Evidence (#589) integrations: Handoffs can carry
exact source references now while those owner domains retain their own lifecycle and schemas.

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
references are identifiers/revisions/digests only.

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
unchanged.

## Security invariants

- Handoffs do not grant permissions or capabilities.
- Referenced data must pass normal source-resource authorization.
- Missing/stale references fail explicitly.
- Historical Handoff revisions are immutable.
- Exact Agent/Team revisions are pinned.
- No private chain-of-thought is stored.
- No external message broker or paid service is required for the reference path.
