# Canonical Agent Handoffs

Issue #592 adds a small platform-owned contract for explicit work transfer between canonical
Agents and Agent Teams. Issue #651 connects that contract to the normal production-shaped
single-node runtime. A Handoff records what a producer intentionally passes to the next work
boundary. It does **not** become a Task, Step, Run, conversation, message bus or workflow
lifecycle.

## Ownership boundary

Canonical execution ownership remains unchanged:

```text
Task -> Plan -> Step -> Run
          |
          +-- dependency/progression authority: durable Plan/Step coordinator

Agent/AgentTeam revision -> AgentRun
          |
          +-- identity/execution authority: Agent runtime

Artifact / Result / Research / Skill / Context resources
          |
          +-- source authority: their owning services/repositories

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

## Production composition (#651)

The public `deployment.build_single_node_deployment(...)` path now exposes one durable
`deployment.handoffs` composition. It wires existing platform authorities together rather than
creating a parallel runtime:

```text
SQLiteHandoffRepository
        |
HandoffService
        |
CoordinatedHandoffService ----> canonical CoordinatorRepository
        |
ProductionHandoffRuntime ------> canonical AgentRepository / AgentRun evidence
        |
        +--> CanonicalHandoffReferenceGateway --> #15 authorization
        |                                      --> Verification Artifact/Result evidence
        |                                      --> Research repository
        |                                      --> Skill repository
        |                                      --> ContextBundle repository
        |
        +--> ContextAssemblyService
                |
                +--> DurableConsumedHandoffContextAdapter
                |
                +--> ContextBoundAgentRuntime --> AgentRun + ContextRunBinding
```

The normal single-node profile owns these restart-safe paths under its existing database
directory:

- `handoffs.sqlite3` for immutable Handoff revisions, idempotency and consumption bindings;
- `research.sqlite3` for canonical Research source/claim/evidence state used by Handoff refs;
- `skills.json` for canonical Skill/SkillBundle state;
- `context-bundles.json` for immutable effective Context Bundles;
- `context-run-bindings.json` for AgentRun -> ContextBundle evidence.

No external broker, paid service or provider-private Handoff store is required.

## Creation semantics

`ProductionHandoffRuntime.create_handoff(...)` is the production entry point. It first binds the
producer to canonical AgentRun evidence and replaces caller-supplied creation provenance with
runtime-owned provenance. Source reads are prepared through `CanonicalHandoffReferenceGateway`
and #15 before the synchronous #592 service persists anything.

`HandoffService.create_handoff(...)` then verifies:

1. the exact producer Agent/Team revision exists;
2. an exact intended consumer revision exists when one is pinned;
3. every referenced source exists at the requested revision/digest when supplied;
4. the producer is allowed to read every referenced source;
5. a pinned consumer is already allowed to read every referenced source.

Creation uses an idempotency key. Reusing the same key with the same semantic content returns
the existing Handoff. Reusing it with different content fails with a canonical conflict.
Revision creation uses optimistic `expected_previous_revision` semantics so a stale producer
cannot silently replace a newer revision.

The service does not advance Steps or create Runs. Those operations remain owned by the
canonical planning/coordinator path.

## Durable Plan/Step and AgentRun binding

`CoordinatedHandoffService` is the read-only integration seam with the durable #384 coordinator.
The production runtime additionally checks canonical #33 `AgentRunRecord` evidence.

Before creation the combined path proves that:

- the referenced Plan belongs to the referenced Task;
- producer and consumer Steps exist in that canonical Plan;
- the producer Step coordination record belongs to the same Task/Plan;
- `producer_run_id` is the producer Step's canonical latest Run;
- the exact producer Agent/Team revision is the participant recorded for that Run.

Before consumption it proves that the consumer Step still belongs to the same Task/Plan and
that the supplied consuming Run is the consumer Step's canonical latest Run. If an AgentRun for
that consuming Run already exists, it must match the exact consumer Agent/Team revision. The
final context-bound Agent execution is checked again after `AgentRunRecord` creation.

For a Team consumer, the selected member must belong to the exact Team revision. The resulting
AgentRun retains both the exact member Agent revision and exact Team revision.

These checks are read-only with respect to Task/Plan/Step progression. Handoffs cannot activate
Plans, progress Steps, create canonical Runs, assign Agents, retry work or repair coordinator
state.

## Consumption and ContextBundle execution

The production path is:

```text
Producer AgentRun
  -> AgentHandoff
  -> durable HandoffConsumption
  -> ContextAssembly
  -> AGENT_HANDOFF ContextEntry
  -> ContextBoundAgentRuntime
  -> Consumer AgentRun
  -> ContextRunBinding
```

`ProductionHandoffRuntime.consume_handoff(...)` rechecks the exact consumer and every source
reference before the #592 service durably records the binding. `start_consumer(...)` then uses a
repository-backed Handoff context adapter, assembles the canonical ContextBundle and refuses to
start the consumer Agent unless that bundle contains the exact Handoff ID, revision and digest.

The final AgentRun carries the Handoff ID/revision/digest in its verification context and the
`ContextRunBinding` records the exact immutable ContextBundle used by that AgentRun.

## Handoff versus Context Bundle

The #590 integration keeps the two lifecycles separate while making the Handoff an actual
canonical context source:

- `AgentHandoff` remains the semantic work-transfer artifact and canonical source authority.
- `HandoffContextSource` exposes the exact Handoff ID, revision, digest and execution references.
- `ContextSourceType.AGENT_HANDOFF` gives #590 a stable source vocabulary without introducing a
  Handoff-specific resolver or permission system.
- `ConsumedHandoffContextAdapter` remains useful for already-materialized runtime contexts.
- `DurableConsumedHandoffContextAdapter` is the production/recovery adapter. It reconstructs
  eligible Handoff context from the canonical Handoff repository and exact consuming Run after a
  restart; no orchestrator session memory is needed.
- The adapter snapshots the compact structured Handoff itself as deterministic inline context,
  so `ReferenceContextRenderer` and context-bound Agent execution can consume it without a
  special content provider.
- The Context source identity retains the canonical Handoff digest; the Context entry's own
  content digest protects the rendered snapshot. These are deliberately separate identities.
- Producer statements enter the Context Bundle with `UNTRUSTED` trust and `CONTEXT` role, so a
  Handoff cannot acquire security/instruction authority merely by being included.
- Referenced Artifacts, Results and Research Evidence are not copied into the Context Bundle by
  this bridge. Their owning domains and normal authorization/verification boundaries remain
  authoritative.

The #590 resolver applies its normal read-authorization gate before including the Handoff.
A denied mandatory Handoff fails closed. Collection is scoped to the exact Task, consuming Run,
Plan/consumer Step and exact Agent revision; Team consumers additionally require membership in
the exact Team revision.

`HandoffConsumption.context_bundle_ref` remains available when an exact Context Bundle is
already known at the consumption boundary. It is not a second ownership mechanism: the final
#590 `ContextRunBinding` remains the canonical proof of which complete Context Bundle an
AgentRun actually used, while Handoff consumption independently proves which exact Handoff
revision was bound to that Run.

## Persistence and restart recovery

`HandoffRepository` is a narrow replaceable seam. The reference implementations are:

- `InMemoryHandoffRepository` for deterministic unit/contract tests;
- `SQLiteHandoffRepository` for durable local/single-node operation.

SQLite persistence stores immutable Handoff revisions, idempotency records and exact consuming
Run bindings. Re-opening the repository reconstructs the canonical Handoff through the
versioned codec and preserves consumption evidence.

The #651 recovery path deliberately does not require an in-memory `HandoffRuntimeContext`.
`DurableConsumedHandoffContextAdapter` reads durable Handoff + consumption state for the exact
Run, reconstructs the canonical `HandoffRuntimeContext`, and contributes the same Handoff source
to normal Context assembly. Context Bundles and ContextRunBindings are durable independently, so
recovery does not depend on which orchestrator adapter was active before the process stopped.

## Authorization and source dereferencing

`CanonicalHandoffReferenceGateway` is the production implementation of the narrow
`HandoffReferenceGateway` seam. Because #592's stable service API is synchronous while canonical
source resolution and authorization are asynchronous, the gateway uses an operation-local
prepared-read scope:

1. resolve the exact source in its owning domain;
2. compare requested revision/digest when supplied;
3. issue the canonical #15 read decision for the actual Agent actor;
4. only after an `ALLOW` decision expose that exact reference to synchronous `HandoffService`;
5. clear the prepared-read scope when the operation ends.

Therefore discovery is never treated as permission and a source cannot remain implicitly
readable across unrelated operations. Consumption repeats the source checks. Missing, stale or
unauthorized references fail explicitly.

Artifact/Result identity is resolved through the canonical Verification evidence resolver;
Research Claim/Evidence, SkillBundle and ContextBundle references resolve through their owning
repositories. Handoffs store identifiers/revisions/digests only and never transport protected
source payloads or secret values.

## Late-bound consumer requirements

`CanonicalConsumerRequirementEvaluator` implements provider-neutral late binding without a new
assignment system. Supported requirements are derived only from exact canonical Agent/Team
facts:

- `role:<role>`;
- `capability:<capability-id>`;
- `policy:<policy-ref>`;
- `agent:<agent-id>`;
- `team:<team-id>`.

Unknown requirement vocabularies fail closed. Regardless of how eligibility is evaluated, the
actual chosen consumer Agent/Team revision is persisted exactly in `HandoffConsumption`.

## Observability and Control Plane

The public single-node composition uses `TelemetryHandoffAuditSink`, projecting Handoff audit
evidence into the existing canonical observability timeline rather than creating a second event
system. The normal lifecycle includes:

- `handoff.created`;
- `handoff.consumed`;
- `handoff.reference_denied` for failed source dereferencing on the consumption path.

The canonical Control Plane registers read-only collections:

- `agent-handoffs` for exact Handoff detail plus Task/Step-filtered history;
- `agent-handoff-consumptions` for exact consumption history;
- `context-bundles` for effective ContextBundle evidence;
- `context-run-bindings` for AgentRun -> ContextBundle evidence.

No Handoff mutation command is registered. The Handoff projection includes:

- producer and intended-consumer revisions or late-bound requirements;
- actual consuming Agent/Team revision for each consumption;
- objective/completed work;
- unresolved questions/blockers;
- exact referenced source identities/revisions/digests;
- Handoff digest/revision;
- consuming Run IDs;
- canonical creation provenance.

Control Plane authorization remains the canonical #15 boundary; the standalone
`HandoffControlPlaneProjection` remains available when a caller needs a narrower
`HandoffViewAuthorizer` projection.

## Orchestrator replacement

No canonical Handoff field contains an orchestrator/provider-private runtime identity. The
canonical bundle is assembled before the Context-aware orchestrator adapter maps the Agent.
Replacing one `ContextAwareOrchestratorAdapter` with another therefore preserves:

- Handoff ID, revision and digest;
- producer/consumer revisions;
- consuming Run binding;
- ContextBundle ID/digest;
- ContextRunBinding evidence.

The #651 acceptance suite exercises two distinct real Context-aware adapters against the same
canonical Handoff-bearing bundle. Neither adapter owns recovery state.

## Security invariants

- Handoffs do not grant permissions or capabilities.
- Referenced data must pass normal source-resource authorization.
- ContextBundle inclusion is separately authorization-gated and fail-closed when mandatory.
- Producer Handoff statements remain untrusted context rather than verification authority.
- Missing/stale references fail explicitly.
- Historical Handoff revisions are immutable.
- Exact Agent/Team revisions are pinned and checked against AgentRun evidence.
- Runtime provenance is platform-generated at the production creation boundary.
- No private chain-of-thought is stored.
- No external message broker or paid service is required for the reference path.
