# Canonical Context Bundle operationalization

Issue #650 makes the canonical Context Bundle boundary introduced by #590 part of the normal
single-node Agent execution path. #590 remains the sole authority for Context identity, resolution,
budgeting, rendering invariants and persistence semantics; this integration layer only composes
existing source owners, routing, egress, execution, inspection and restart recovery around it.

## Production-shaped execution path

The public `ai_multi_agent_platform.deployment.build_single_node_deployment(...)` composition now
installs canonical Context after the durable Connector and Planning owners exist. Agent-bound Runs
therefore follow this path:

1. the canonical Task/Run lifecycle resolves the exact Agent revision and optional Plan/Step binding;
2. source-domain adapters project authorized candidates from Task, Agent, Plan/Step, Skill Bundle,
   Research Evidence, completed Verification findings, Repository state, File/Artifact/Result
   references, Memory and Knowledge;
3. `OperationalContextAssemblyService` converts source-provider absence/failure into explicit
   `UNAVAILABLE` observations and delegates selection truth to the #590 `ContextResolver`;
4. the immutable Bundle is persisted before execution;
5. Bundle token usage plus the explicit output reserve becomes a server-owned
   `runtime_model_requirements.min_context_window` constraint for #10 routing;
6. the selected model location determines whether rendering stays local or crosses the canonical
   Context egress boundary;
7. the exact rendered Bundle becomes the real model system/user input; legacy adapter-private
   `task_context` / `project_context` dictionaries are rejected on this path;
8. the AgentRun is bound immutably to the exact Bundle ID and digest;
9. Run/AgentRun/Context evidence remains inspectable through the Control Plane and survives restart.

Non-Agent execution profiles keep their existing lifecycle delegate and are not forced through the
Context boundary.

## Durable local state

The reference single-node profile remains self-hosted and does not require a paid Context, RAG,
vector-search or model service. The operational composition stores the following state under the
normal deployment database directory:

- `context-bundles.json` — immutable effective Bundle evidence;
- `context-run-bindings.json` — AgentRun → Bundle identity/digest bindings;
- `memory.sqlite3` — canonical local Memory source state;
- `knowledge.sqlite3` — canonical local Knowledge source state;
- `skills.json` — canonical Skill revisions/Bundles/bindings;
- `research.sqlite3` — canonical Research Evidence state.

File, Task/Run, Agent, Coordination/Planning, Verification and Repository state continue to use their
existing canonical owners. In particular, Verification remains in the existing #86 durable service;
the Context adapter only projects completed result/findings evidence and never creates a shadow
Verification store. Context adapters do not create shadow copies of those lifecycles.

## Source availability semantics

Operational adapters are registered through `ContextSourceAdapterBinding` with explicit expected
source identity and mandatory/optional semantics.

- optional provider failure or configured absence becomes a canonical `UNAVAILABLE` candidate and
  then an auditable omission;
- mandatory provider failure becomes a `MANDATORY_UNAVAILABLE` resolution blocker;
- `NOT_FOUND` is recorded as missing-source evidence rather than being confused with a transient
  provider failure;
- authorization, project/workspace scope, freshness, trust, conflict and budget rules remain owned
  by the #590 resolver;
- untrusted retrieved content remains Context/Evidence and cannot acquire Security/Instruction
  authority;
- completed Verification findings are optional `VERIFICATION` / `EVIDENCE` candidates and reviewer
  prose is deliberately `UNTRUSTED`;
- explicit user-provided task intent is already carried by the exact canonical Task revision through
  `TaskContextSourceAdapter`; no second Human/user-message Context store is introduced;
- secret values are never promoted out of canonical secret references.

## Routing and output reserve

`OperationalContextBoundAgentRuntime` derives a server-owned routing requirement from the resolved
Bundle via `context_window_requirement(...)`. The configured single-node output reserve is 2,048
tokens. This requirement is merged monotonically with existing Agent/profile/Task requirements.

The Context-derived requirement is deliberately not represented as a Task model override. It is a
runtime safety constraint and therefore does not depend on the Agent revision's
`allow_task_override` setting.

## Egress

`ModelRegistryContextEgressTargetResolver` maps canonical model location to the egress posture:

- `LOCAL`: no outbound Context export;
- `SELF_HOSTED`: internal `CONTEXT_EXPORT` boundary;
- `REMOTE`: external `CONTEXT_EXPORT` boundary.

`ContextBundleEgressExporter` evaluates the strongest Bundle classification before rendering. The
canonical egress policy fails closed for secret/secret-reference/regulated material crossing an
external target unless that target has an explicit matching sensitive-data policy. Secret
references remain references and are not ordinary Context values.

## Control Plane and Web inspection

The normal single-node Control Plane registers two read-only Context collections:

- `context-bundles`
- `context-run-bindings`

Bundle detail projections include Bundle/Task/Run/Agent/Plan/Step/Skill identity, source provenance,
revisions/digests where authorized, omissions, budget/usage, resolver/policy versions and the
reproducibility-limited flag. Inline Context content is never returned by the ordinary northbound
inspection route.

Entry provenance is re-authorized against the underlying source through #15. A caller that can read
the Bundle but not the source receives a redacted entry without source ID, revision, locator or
digest. Omission detail text is also suppressed on the northbound route.

The Web Run detail surface resolves:

`Run -> context-run-bindings -> context-bundles`

and displays the canonical binding identity, Bundle digest, routing/budget evidence, source classes,
freshness/classification, omissions and redaction state.

## CLI inspection

No second Context-specific transport is required. The existing generic extension CLI reaches the
same Control Plane resources, for example:

```text
platform extension list context-run-bindings --filter run_id=<run_id>
platform extension show context-run-bindings <agent_run_id>
platform extension list context-bundles --filter run_id=<run_id>
platform extension show context-bundles <context_bundle_id>
```

This keeps CLI behavior API-first and prevents Context inspection from bypassing authorization or
redaction.

## Restart reconciliation

`ContextBindingReconciler` scans persisted AgentRuns during public single-node composition. It only
repairs a missing binding when the AgentRun's persisted orchestrator mapping names the exact Bundle
ID and digest and the durable Bundle still matches that digest.

Reconciliation is idempotent. Conflicting or incomplete evidence fails closed instead of inventing a
new binding. Existing correct bindings are preserved unchanged.

## Acceptance coverage

The combined #650/#680 operational regression evidence is:

- `tests/integration/context/test_operational_context.py`
  - optional vs mandatory source unavailability;
  - server-owned Context-window requirement merging;
  - external secret-reference egress denial;
  - Control Plane source redaction;
  - idempotent crash/restart binding repair;
- `tests/integration/context/test_source_adapters.py`
  - exact Task and Agent revision/instruction projection;
  - Skill Bundle identity/digest projection;
  - Research Claim/Evidence provenance and untrusted authority;
  - Repository Run provenance and bounded immutable source slices;
  - File/Artifact/Result reference-oriented projection;
  - scoped Memory and Agent-allowlisted Knowledge retrieval;
- `tests/integration/context/test_routing_and_rendering.py`
  - Context-derived `min_context_window` causes #10 routing to reject an undersized model;
  - local/reference rendering preserves Bundle identity without an egress dependency;
  - canonical Context mapping rejects legacy task/project context mixing;
- `tests/e2e/context/test_single_node_agent_context.py`
  - public durable single-node composition;
  - real first Agent task through Context assembly/rendering/model input;
  - persisted AgentRun → Bundle binding;
  - Control Plane inspection;
  - restart restoration/reconciliation;
- `tests/integration/context/test_verification_evidence_projection.py`
  - completed Verification finding/result → canonical `VERIFICATION` evidence;
  - expired Verification result projects as stale evidence;
  - pending Verification is not misrepresented as evidence;
  - reviewer-owned arbitrary metadata is not copied into Context;
- `tests/integration/context/test_task_context_ownership.py`
  - explicit user objective remains Task-owned and exact-revision bound;
- `tests/e2e/context/test_source_revision_persistence.py`
  - the public single-node binding factory includes the Verification source adapter;
  - a real Task revision changes the Bundle digest while the historical Bundle remains stable across restart;
- `tests/unit/cli/test_cli_context_inspection.py`
  - generic API-first CLI Run → binding → Bundle tracing;
  - ordinary CLI inspection never exposes inline Context values;
- `frontend/src/api/context.test.ts`
  - Run → binding → Bundle frontend reads through the canonical extension collections.

Together these tests exercise the full public AgentRun path plus focused live-source conformance
without creating a duplicate Context-specific runner or requiring a paid external service.
