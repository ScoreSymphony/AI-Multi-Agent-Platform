# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-02

This roadmap defines the recommended implementation order for the AI Multi-Agent Platform from the current repository state toward the ideal end product.

It replaces simple issue-number ordering. GitHub issue numbers are identifiers only. The actual order is determined by canonical ownership, hard dependencies, contract stability and safe parallelization.

The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

## 1. Execution rules

### 1.1 Hard dependencies are blockers

An issue may start when its explicit hard dependencies are implemented/merged sufficiently for the target work to use their canonical contracts.

`Follow-up integrations`, related issues and future extension references are not blockers unless explicitly stated.

### 1.2 Contracts merge before consumers

When parallel branches depend on a shared contract, merge in this order:

1. canonical contract/domain change;
2. reference implementation + contract tests;
3. adapters/providers;
4. Control Plane/API extension;
5. UI/CLI/client integration;
6. deployment/operations integration.

### 1.3 Parallel work must preserve ownership

Parallelization is valid only when branches do not independently redefine the same canonical concept.

Examples:

- Model work may proceed in parallel with File/Memory/Knowledge work.
- Control Plane foundation may proceed in parallel with model/provider work because it exposes only existing Task/Run contracts.
- Hermes must not define AgentDefinition while #33 is still defining the canonical Agent model.
- A browser adapter must consume the Capability/File/Security contracts rather than inventing private equivalents.

### 1.4 Start gates and completion gates may differ

Some broad issues can begin with their foundational work before every downstream subsystem exists, but they cannot be considered complete until their stated acceptance criteria are satisfied.

This is especially relevant for UI, observability, security and end-to-end conformance.

## 2. Foundation already completed

The first architecture foundation is complete through #7:

- #1 product/architecture principles
- #2 repository baseline
- #3 upstream/license/provenance policy
- #4 canonical domain model
- #5 replaceable core interfaces/contracts
- #6 canonical Task/Run/Event kernel
- #7 reference execution path and execution boundaries

These issues form the base for all remaining work.

---

# 3. Current execution frontier — start now

The following workstreams are intentionally parallel and should be the immediate implementation focus.

## Lane A — Model foundation

### #10 — Model provider abstraction, Model Registry and Model Router

Purpose:
- establish `ModelProvider`;
- establish canonical `ModelRegistry`;
- establish deterministic `ModelRouter`;
- provide a local/self-hosted OpenAI-compatible reference provider.

This is a major prerequisite for #11, #14, #33 and later agent/model features.

### Then: #11 — Optional LiteLLM adapter

#11 starts after #10 contracts are stable enough for an adapter to implement them.

LiteLLM must remain optional.

---

## Lane B — Capability/tool foundation

### #12 — Capability Registry and MCP adapter

Purpose:
- canonical Capability model;
- capability/provider registry;
- canonical invocation pipeline;
- native deterministic reference tool;
- optional MCP adapter.

This is a major prerequisite for #15, #20, #33, #74 and many later integrations.

---

## Lane C — Data boundaries

### #13 — Persistence, Files, scoped Memory and Knowledge boundaries

Purpose:
- separate canonical persistence from Files, Memory and Knowledge;
- define the six memory scopes;
- supply local/reference providers;
- establish canonical IDs and provenance rules.

This unlocks #15 and #37 directly and is also foundational for Agents, Browser, Search, Import/Export and Workspaces.

---

## Lane D — Control Plane foundation

### #32 — Versioned Control Plane foundation and Task/Run API

Purpose:
- API versioning;
- canonical errors;
- Task/Run endpoints;
- canonical event/timeline surface;
- live-update foundation;
- extension rules for later domain APIs.

Important: #32 must **not** wait for future domains such as Agents, Workers, Automations or Plugins. Those issues extend the Control Plane after their own contracts exist.

---

## Lane E — Configuration and secrets foundation

### #34 — Configuration, credentials and secrets management

Purpose:
- deterministic configuration hierarchy;
- `SecretReference` and replaceable `SecretProvider`;
- safe redaction/introspection;
- least-privilege secret delivery hooks.

This can proceed independently from the full worker/auth/plugin stack.

---

## Lane F — Messaging foundation

### #35 — Event transport and internal messaging architecture

Purpose:
- separate canonical Domain Events from transport messages;
- define versioned envelope/delivery semantics;
- provide deterministic in-process reference transport;
- prepare later distributed transport.

This can be built before the distributed scheduler or Automations.

---

## Lane G — Forge/legacy capability recovery

### #9 — Audit and selectively port reusable Forge capabilities

Purpose:
- recover validated execution/recovery/idempotency behavior;
- preserve provenance;
- reject old architecture assumptions;
- place reused behavior behind the canonical Executor boundary.

This work can proceed in parallel with #10/#12/#13/#32/#34/#35 because #1–#7 already define the canonical ownership boundary.

---

## Lane H — Security architecture baseline

### #43 — Threat model and security baseline

The threat model should begin early, not after the entire platform is complete.

Early work establishes:
- assets and trust boundaries;
- threat categories;
- security invariants;
- secure-default requirements;
- regression-test patterns;
- requirements that downstream issues must satisfy.

Later subsystem work extends the same threat model for workers, plugins, browser, connectors, updates and distributed execution.

Security is therefore a cross-cutting lane, not a final cleanup phase.

---

# 4. Convergence Gate 1 — canonical policy and workspace foundations

This gate begins as soon as the relevant Lane B/C/D contracts are merged.

## #15 — Identity, permissions and approvals

Hard prerequisite path:

`#12 + #13 -> #15`

#15 owns the canonical authorization and approval vocabulary. It should not wait for the finished distributed worker runtime or full observability system.

## #37 — Workspace and project environment management

Hard prerequisite path:

`#13 + completed #7 -> #37`

#37 creates portable canonical workspaces/snapshots and is required before distributed jobs and repository integration can safely materialize source trees.

## #11 — LiteLLM adapter

Hard prerequisite path:

`#10 -> #11`

This remains an adapter lane and should not block canonical model work.

---

# 5. Convergence Gate 2 — core platform services

Once #15 and the required foundation contracts exist, several major features become parallelizable.

## Lane I — Authentication

### #36 — Authentication and session management

Path:

`#15 + #32 -> #36`

Authentication establishes human/service/worker identity. Authorization remains owned by #15.

---

## Lane J — Agent/Team canonical runtime

### #33 — Agent definitions, profiles and teams

Path:

`#10 + #12 + #13 + #15 -> #33`

This issue **precedes full Hermes integration**.

Required direction:

`Canonical Agent/Team -> orchestrator adapter mapping`

not:

`Hermes agent/session -> canonical Agent`

---

## Lane K — Distributed compute

### #14 — Node registry, worker protocol and scheduler

Path:

`#10 + #12 + #15 + #37 + completed #7 -> #14`

#14 defines the same scheduling path for local and remote workers.

It should not wait for #16 observability; instead it emits telemetry that #16 later consumes.

---

## Lane L — Automations

### #18 — Automations, triggers and event-driven Task creation

Path:

`#15 + #32 + completed #6 -> #18`

Automations must create canonical Tasks. They do not call workers/orchestrators directly.

Distributed messaging from #35 is an optional integration, not the definition of Automation.

---

## Lane M — Plugin runtime

### #20 — Plugin system and extension registry

Path:

`#12 + #15 + completed #3/#4/#5 -> #20`

#20 is deliberately limited to runtime extension architecture and plugin lifecycle.

Templates, portable Import/Export and the optional Registry/Marketplace are separate later issues.

---

## Lane N — Browser capability

### #74 — Replaceable Browser/Web capability

Path:

`#12 + #13 + #15 + completed #7 -> #74`

A browser implementation consumes canonical Capability/File/Security contracts and remains replaceable.

---

## Lane O — Terminal/session foundation

### #73 — Terminal and execution-session interface

Path:

`#15 + #32 + #37 + completed #7 -> #73`

The local/reference session path may be built before remote worker sessions exist. #14 later extends this to remote workers.

---

# 6. Convergence Gate 3 — orchestration, connectors and observability

## #8 — Hermes orchestrator adapter

Path:

`#10 + #12 + #33 + completed #3–#7 -> #8`

Hermes consumes canonical Agent/Team/Task contracts. It is not permitted to define them.

## #44 — Connector and external-integration framework

Path:

`#12 + #13 + #15 + #34 + #36 -> #44`

Connector semantics are independent from plugin packaging and distributed messaging.

#18 may later consume connector events; #20 may later package connector implementations.

## #16 — End-to-end observability

Completion path:

`#10 + #12 + #14 + #15 + completed #4–#6 -> #16`

Instrumentation conventions may be prepared earlier, but #16 reaches its full Definition of Done only after representative model/tool/worker paths exist.

---

# 7. Product feature expansion — parallel after Gate 3

Once Agents, Authentication, Workers, Connectors and Observability exist, a broad product layer can proceed concurrently.

## #72 — Task-centric Chat

Path:

`#10 + #13 + #15 + #32 + #33 + #36 -> #72`

Chat remains an interaction surface; durable work becomes canonical Tasks.

## #75 — Notifications

Path:

`#15 + #32 + #36 + completed #6 -> #75`

In-app notification baseline first; external delivery later via connectors.

## #77 — Standard Agents and starter Agent Teams

Path:

`#10 + #12 + #15 + #33 -> #77`

These are ordinary editable canonical definitions, never hard-coded roles.

## #82 — Provider-neutral Repository/Git integration

Path:

`#12 + #13 + #15 + #34 + #37 + #44 -> #82`

Local Git is the self-hosted baseline; hosted providers remain connector implementations.

## #45 — Platform-wide Search and resource discovery

Path:

`#13 + #15 + #20 + #32 + #33 + #44 -> #45`

Search indexes are never canonical state.

## #78 — Reusable Templates

Path:

`#10 + #12 + #15 + #18 + #20 + #33 + #37 -> #78`

## #79 — Portable Import/Export

Path:

`#13 + #15 + #18 + #20 + #33 -> #79`

Portable packages are distinct from operational backup/restore.

---

# 8. Client, evaluation and operational control layer

These issues should be developed in parallel once their underlying canonical APIs are available.

## #17 — Initial Web UI shell

The shell/API-client/navigation architecture may begin after #32.

Its functional pages are progressively completed as #10/#12/#13/#14/#15/#16/#33 and later APIs become available.

Do not block all frontend architecture work until every backend feature is complete, but do not mark #17 complete until its acceptance criteria can be exercised against canonical APIs.

## #38 — CLI and administrative interface

The client architecture may begin after #32. Full administrative/security/diagnostic behavior converges after #15/#16/#34/#36.

## #19 — Evaluation and regression framework

Primary completion path:

`#10 + #12 + #16 + completed #4/#6 -> #19`

The deterministic evaluation model can be developed incrementally, but system-level measurements become more valuable once observability exists.

## #76 — Usage and resource accounting

Path:

`#10 + #14 + #16 + #32 -> #76`

## #21 — Durable workflow engine decision

Path:

`#9 + #14 + #18 + completed #1/#4/#5/#6 -> #21`

This remains an ADR/evidence issue. Temporal or another engine must not be adopted first and justified later.

---

# 9. Deployment, recovery and lifecycle operations

## #39 — Deployment profiles and self-hosted installation

Completion path:

`#14 + #16 + #32 + #34 + #36 + #38 -> #39`

Deployment may prototype earlier, but supported production profiles should be based on real canonical worker/auth/health/control interfaces.

## #40 — Backup, restore and disaster recovery

Path:

`#13 + #20 + #34 + #38 + #39 + completed #6 -> #40`

## #41 — Database migrations and platform upgrade lifecycle

Path:

`#13 + #20 + #32 + #40 + #79 -> #41`

#41 owns compatibility across persisted schema, APIs, plugins and portable formats.

## #42 — Release/update/upstream synchronization

Path:

`#8 + #9 + #11 + #19 + #20 + #41 + completed #3 -> #42`

This is intentionally late because release compatibility claims require real adapters, migrations and regression evidence.

---

# 10. Optional distribution layer

## #81 — Optional Registry / Marketplace

Path:

`#20 + #78 + #79 + #15 + completed #3 -> #81`

This is explicitly optional and must never become a baseline dependency.

---

# 11. Final cross-platform acceptance

## #46 — End-to-end Conformance and Platform Acceptance Suite

#46 is the final system acceptance gate.

It validates the ideal end product across:

- canonical lifecycle;
- reference-only local operation;
- Hermes;
- Forge execution;
- local model/tool paths;
- distributed workers;
- approvals/security;
- restart/recovery;
- Automations;
- UI/CLI;
- Chat;
- Terminal;
- Browser;
- Notifications;
- Usage/Resources;
- Standard Agents/Teams;
- Templates;
- Import/Export;
- optional Registry-disabled and Registry-enabled paths;
- Repository/Git;
- migration/backup/release compatibility.

No earlier issue should redefine canonical contracts merely to make #46 easier to pass.

---

# 12. Recommended parallel agent allocation

With several coding agents available, use capability lanes instead of assigning consecutive issue numbers.

## Immediate allocation example

### Agent A — Models
- #10
- then #11

### Agent B — Capabilities/Tools
- #12

### Agent C — Data/Memory/Knowledge
- #13
- then #37 when ready

### Agent D — Control Plane
- #32
- later API extensions from downstream domains

### Agent E — Configuration/Security plumbing
- #34
- support #15/#36 integrations later

### Agent F — Messaging
- #35

### Agent G — Forge reuse
- #9

### Agent H — Security architecture
- #43 threat model/security baseline
- continuously review security-sensitive downstream PRs

Do not assign #8 Hermes to an agent as full implementation before #33 has produced canonical Agent/Team contracts.

Do not assign #14 as full implementation before #15 and #37 are sufficiently stable.

## After first convergence

Suggested parallel allocation:

- Security/Authorization agent: #15
- Workspace agent: #37
- Authentication agent: #36
- Agent runtime agent: #33
- Scheduler/worker agent: #14
- Automation agent: #18
- Plugin agent: #20
- Browser agent: #74
- Terminal agent: #73

Then converge into #8, #44 and #16.

---

# 13. Critical-path summary

The longest architecture-critical chain is approximately:

`#12/#13 -> #15 -> #37/#36 -> #14/#44 -> #16 -> #38 -> #39 -> #40 -> #41 -> #42 -> final #46`

A second major chain is:

`#10/#12/#13/#15 -> #33 -> #8`

A third product-portability chain is:

`#15/#18/#20/#33 -> #78/#79 -> #81`

These chains explain why work should be parallelized aggressively at the beginning rather than performed by issue number.

---

# 14. Definition of roadmap health

The roadmap is healthy when:

- no canonical-domain issue hard-depends on a concrete adapter that should consume it;
- no low-level contract waits on a high-level UI/observability consumer;
- optional adapters/plugins/registry components do not block baseline operation;
- single-node/reference paths exist before distributed/production adapters are required;
- every broad cross-cutting issue distinguishes foundational work from later integrations;
- parallel branches can merge without independently inventing the same canonical types;
- #46 can eventually validate the complete ideal product without introducing new architecture during acceptance.

Update this roadmap whenever a material dependency direction changes, an issue is split/merged, or an ADR changes the implementation graph.
