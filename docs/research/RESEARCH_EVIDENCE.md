# Canonical Research Evidence

Issue #589 adds a platform-owned semantic layer for source-based research without creating a
second Task/Run runtime or replacing existing Search, Browser, Connector, File, Knowledge,
Memory, Planning, Verification or Authorization ownership.

## Canonical flow

```text
Task / Plan / Run
      |
      v
ResearchItem
      |
      +--> SourceRecord --> SourceObservation
      |                         |
      |                         v
      +--> Claim <-------- EvidenceRecord
              |
              v
        #86 Verification
              |
              v
      ResearchActionContext
              |
              +--> #439 Planning
              +--> explicit Knowledge promotion
              +--> explicit Memory promotion
```

A Research Item is investigation metadata. Its status never replaces canonical Task status.
Researcher and Reviewer remain ordinary Agents. Source acquisition remains an ordinary platform
capability and all execution remains subject to the existing Workspace, Executor, Capability and
Authorization boundaries.

## Source identity and observations

`SourceRecord` is the stable identity for a research source locator. Reading that source produces
an immutable `SourceObservation` containing the strongest source identity available at that time:

- revision/version/commit/ETag when the source exposes them;
- content/snapshot digest when captured;
- retrieval timestamp;
- snapshot Artifact when one exists;
- explicit `UNVERIFIABLE` state when immutable identity cannot be established.

The same URL or repository can therefore have many observations. Changed content creates a new
observation; the previous observation is not rewritten.

Repository Intelligence (#502) is a first-class source producer. Research consumes its existing
`RepositoryIntelligenceProvenance` (`repository_id`, requested/resolved revision, provider and
freshness) rather than inventing a second code-intelligence provenance model. Git/source state and
captured snapshots remain authoritative for exact source identity.

## Claims and Evidence

A `Claim` is a structured proposition, not project truth. Its status is an auditable Research
projection such as proposed, supported or disputed.

An `EvidenceRecord` is small immutable metadata linking one Claim to one exact SourceObservation.
It records the observed source revision/hash fields needed to explain the historical conclusion.
Large pages, PDFs, screenshots, logs, repositories and datasets remain Files/Artifacts rather than
being copied into the Research record itself.

Evidence relations are explicit:

- `supports`;
- `contradicts`;
- `contextualizes`;
- `derives_from`.

Supporting Evidence does not itself approve a decision. Unsupported, disputed or stale Claims are
blocked from the governed Research-to-Action bridge.

## Freshness and revalidation

Freshness is evaluated against the Research Item's `FreshnessPolicy` and current source
observation. A later source change marks older Evidence stale as a projection; it does not mutate
historical Evidence.

Revalidation creates a new EvidenceRecord pointing at the new SourceObservation and links it with
`supersedes_evidence_id`. The old Evidence remains available to explain earlier decisions.

## Verification boundary

Research uses the existing #86 `VerificationService` as the authority for policy, verifier kind,
reviewer independence and PASS/FAIL semantics. `ResearchVerificationBridge` resolves exact
Research Item/Claim/Evidence revision+digest subjects and records only a current PASS as action
provenance.

The bridge deliberately does **not** bind Research review to
`VerificationCompletionAuthority`. Therefore checking a Research Claim does not accidentally make
that check a Task-completion gate. A Task may independently have its own #86 completion policy.

The current #86 request contract is Task-scoped. Project/domain research therefore needs an
associated canonical Task context when it is sent through #86; this preserves one Task/Run system
rather than introducing a Research execution lifecycle.

## Research to action

`ResearchActionContext` is the governed handoff. It contains the exact Research Item revision and
digest plus the supported Claim, current Evidence and required Verification IDs.

The #439 Planning bridge passes these references through `PlanningService.propose(evidence_refs=)`.
Research code does not create or activate a private Plan and cannot grant permissions or approvals.
Normal #15 authorization and approval policy still governs privileged downstream actions.

## Knowledge and Memory promotion

Research Evidence is not automatically Knowledge or Memory. `ResearchPromotionBridge` requires an
explicit promotion call and carries Research Item/Claim/Evidence/Verification references into the
existing source/provenance metadata.

This preserves the separation:

```text
Claim + Evidence -> Verification -> promotion decision -> Knowledge / Memory / Docs / ADR / Tests
```

Mutable Memory remains a convenience/context store rather than evidence truth.

## Untrusted executable research

`UntrustedResearchExecutionProfile` is a policy projection for existing runtime boundaries. The
reference profile requires:

- read-only acquired source;
- isolated Workspace;
- no network egress by default;
- no platform/provider secrets;
- no production write scope;
- bounded CPU, RAM, disk and PID resources.

The Research package does not implement another container, scheduler or executor. Existing runtime
components consume these constraints when executable research is actually run.

## Control Plane

`register_research_control_plane(...)` registers backend-neutral northbound projections for:

- `research-items`;
- `research-sources`;
- `research-source-observations`;
- `research-claims`;
- `research-evidence`.

The projections intentionally expose the evidence needed to answer *why* a conclusion exists:
Research Item revision/digest, exact source observation identity, source revision/hash/snapshot
binding, Evidence relation and current freshness, Verification bindings, Artifact references and
canonical Task/Plan/Run provenance where present. Sensitive metadata is redacted before it leaves
the domain boundary.

The registered commands are deliberately research-semantic mutations only:

- `research.create`;
- `research.source.add`;
- `research.source.observe`;
- `research.claim.add`;
- `research.evidence.add`;
- `research.evidence.revalidate`.

They do not create Tasks/Runs, approve decisions, grant permissions or execute downloaded code.
Existing Control Plane authorization is applied first, the Research service remains subject to #15,
and the domain registration additionally enforces the authenticated owner scope. Observation
commands reuse the northbound `Idempotency-Key`, so retries cannot silently replace an earlier
source/content binding.

The generic Control Plane extension layer supplies list/get HTTP routes, command routes and OpenAPI
entries for the registered Research resources. Web, CLI and other clients therefore consume the
same canonical resource surface rather than reading the Research repository directly.

## Persistence

`ResearchRepository` owns canonical Research records. The baseline provides an in-memory
implementation and a dependency-free SQLite implementation for single-node restart recovery.

Source Observations, Evidence and completed Verification bindings are append-only historical
records. Research Items and Claims use explicit optimistic revisions for their mutable projections.

## Current #589 scope

The current implementation establishes the canonical model, durability, freshness semantics,
#86 binding, #439 provenance handoff, #502 provenance consumption, explicit Knowledge/Memory
promotion, the untrusted-execution policy contract, and the canonical Control Plane resources and
commands.

Remaining #589 work includes Search registration, research-specific #19 evaluation fixtures,
portability, downstream Decision/provenance integration where the canonical consumers expose the
required seams, Research Team integration coverage, and broader end-to-end coverage. Those
additions must extend the authority boundaries above rather than redefine them.
