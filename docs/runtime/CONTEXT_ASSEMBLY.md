# Canonical Context Assembly

Issue #590 introduces a platform-owned boundary for deciding exactly which authorized, versioned information reaches an Agent/Model Run. The context subsystem is intentionally separate from source-domain lifecycle ownership and from provider/orchestrator prompt rendering.

## Ownership boundary

The canonical flow is:

```text
Task / Plan / Step / Agent / Skill Bundle / Memory / Knowledge
Research Evidence / Repository Intelligence / Files / Results
                         |
                         v
               Context source candidates
                         |
                         v
                  ContextResolver
                         |
                         v
             immutable ContextBundle
                         |
               +---------+---------+
               |                   |
               v                   v
        adapter rendering     Run binding/evidence
               |
               v
        model/orchestrator
```

Source systems remain authoritative for their own records. Context assembly records exact references, revisions, digests, freshness, classification and selection evidence; it does not become a second Task, Skill, Research, Repository, Memory or File store.

The orchestrator or model provider is not allowed to become the hidden context authority. Rendering may translate an already-resolved bundle into a provider-native representation, but it may not silently widen scope, replace mandatory entries, reorder canonical entries or redefine bundle identity.

## Canonical models

`ContextCandidate` is a source-domain observation before effective resolution. It carries:

- a `ContextSourceRef` with source type, source ID and exact revision/digest/snapshot evidence where available;
- role and selection reason;
- mandatory/optional semantics;
- inline content or an exact content reference plus digest;
- freshness and trust state;
- data classification;
- priority/relevance;
- Project/Workspace scope;
- optional conflict and security metadata.

`ContextBundle` is the immutable effective context record. Its canonical digest is computed from the execution identity, ordered entries, omissions, budget/usage evidence, resolver/policy versions, Skill Bundle reference and reproducibility state. Creation time and generated `context_bundle_id` are not part of the digest, so resolving identical canonical inputs remains digest-equivalent.

Changing source revision/provenance, effective content, policy-visible omissions or execution identity changes canonical bundle identity through the digest.

## Resolver policy

`ContextResolver` performs server-owned resolution in this order:

1. Project and Workspace scope filtering;
2. data-classification filtering;
3. freshness/unavailability handling;
4. authorization through the canonical authorization provider;
5. duplicate suppression;
6. conflict handling;
7. deterministic mandatory/optional, role, source, priority and relevance ordering;
8. provider-neutral budgeting and optional explicit truncation;
9. immutable bundle construction.

Mandatory context fails closed when it cannot be supplied safely. The failure is represented by `ContextResolutionError` with a canonical `ContextResolutionBlocker`; optional failures become auditable `ContextOmission` records.

Budget limits are resource constraints, not authorization boundaries. Mandatory entries are sorted ahead of optional enrichment. Optional inline entries may be truncated only through the explicit versioned truncation policy; the resulting entry records the transformation kind, policy version, input digest, output digest and size evidence.

## Source integrations

`ContextSourceAdapter` lets canonical source domains contribute candidates without giving those adapters assembly authority. The reference `StaticContextSourceAdapter` is also sufficient for deterministic local/evaluation fixtures and requires no paid context or RAG service.

The source vocabulary includes Task, Plan/Step, Agent, Skill, Memory, Knowledge, Research Evidence, Repository, File, Artifact, Result, prior Run, Verification, human-provided context and system/security context.

Skill integration preserves the resolved `skill_bundle_id` and digest on the bundle in addition to exact Skill source entries. Research Evidence and Repository Intelligence contribute source revisions/digests/locators through normal candidates; neither subsystem owns final ordering, authorization or budgeting.

## Secret and untrusted-content boundary

`SECRET_REFERENCE` context is reference-only. `ContextCandidate` and `ContextEntry` reject inline secret content, so ordinary `ContextBundle` serialization contains the secret reference/digest evidence rather than the resolved secret value. A renderer may resolve that reference only at an explicitly authorized consuming boundary.

Untrusted retrieved content cannot be promoted to `SECURITY` or `INSTRUCTION` authority merely by appearing in context. Construction rejects that combination before resolution.

## Rendering invariants

`ContextRenderer` is replaceable. `ReferenceContextRenderer` proves the local/reference path without Hermes, LiteLLM or a paid model service.

Every rendered result retains:

- `context_bundle_id`;
- canonical bundle digest;
- canonical entry ordinal;
- content digest;
- mandatory semantics.

`assert_render_preserves_bundle()` rejects renderings that change those invariants. This is the adapter replacement seam: provider/orchestrator-specific formatting can change while canonical context identity remains platform-owned.

## Persistence and Run binding

`InMemoryContextBundleRepository` and `JsonContextBundleRepository` enforce immutable ID/digest storage and digest-equivalent reuse. The JSON implementation provides a local durable reference path and reloads bundles with digest verification.

`ContextRunBinding` records the exact Context Bundle ID/digest used by one AgentRun together with Task/Run/Agent revision, resolver/policy versions and orchestrator adapter identity. `JsonContextRunBindingRepository` preserves that historical evidence across process restart.

`ContextBoundAgentRuntime` composes the existing Agent runtime with the canonical context seam. It rejects mixing a Context Bundle with legacy adapter-private task/project context, renders the already-resolved bundle, starts the Agent through a context-aware mapper and records the resulting Run binding.

## Reproducibility guarantees

A historical run remains explainable even if live source domains later change because the Run binding points to the exact bundle and each entry preserves the strongest available source revision/digest/snapshot evidence. A bundle marks `reproducibility_limited` when an effective entry has no revision, digest or snapshot evidence.

The reference persistence path supports the critical restart case: a bundle can be resolved and durably stored before execution, reloaded after restart, and reused for identical canonical inputs without silently substituting changed/stale/unauthorized live context.

## Control Plane inspection

The context Control Plane resources expose safe projections of bundles and Run bindings. Inspection surfaces are expected to show bundle identity, source references, revisions/freshness, selection/omission reasons, budget usage and resolver/policy versions while respecting entry visibility rules. Secret values are never made visible merely because a bundle contains a SecretReference.

## Required regression evidence

`tests/test_issue_590_context_bundles.py` is the focused #590 regression/acceptance suite. It proves:

- deterministic digest assembly and idempotent storage;
- changed source revision -> changed bundle digest;
- mandatory-before-optional ordering;
- explicit truncation and fail-closed mandatory budget exhaustion;
- unauthorized source exclusion and mandatory authorization blocking;
- cross-Project isolation;
- stale/unavailable source handling;
- duplicate suppression;
- Skill Bundle, Research Evidence and Repository source provenance;
- renderer replacement without canonical identity drift;
- bundle and Run-binding restart persistence;
- absence of secret values from canonical serialization;
- rejection of untrusted retrieved content as instruction authority.

These tests use only local fakes/reference implementations and therefore preserve the self-hosted/no-paid-service requirement.
