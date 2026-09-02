# Platform Security Threat Model

Status: normative baseline for issue #43. This is a living document and must be extended as security-sensitive subsystems land.

## 1. Purpose and scope

The platform coordinates Agents, models, tools, executors, files, external systems and potentially multiple machines. Natural-language content, model output, retrieved content, tool output and external events may be malicious or compromised even when they originate from an otherwise legitimate Task.

The threat model therefore separates **intent** from **authority**. Models may propose actions, but only canonical identity, policy, approval and enforcement paths may authorize them.

This baseline covers the current platform foundation and defines required security hooks for future Workers, plugins, browser/network access, Connectors, imports/exports, registries and update systems. Those later components extend this model; they are not prerequisites for establishing it.

## 2. Security objectives

The platform should preserve:

- confidentiality of user, Project and Workspace data and credentials;
- integrity of canonical Task, Run, Approval, Event, Agent, configuration and provenance state;
- confinement of execution to authorized capabilities, Workspaces and network scopes;
- isolation between users, Projects, Workspaces, providers, Workers and sessions;
- availability against accidental or malicious resource exhaustion within configured limits;
- auditability of security-critical decisions and side effects;
- recoverability after Worker loss, compromise, replay, crash or update failure;
- replaceability of adapters without weakening canonical security ownership.

## 3. Protected assets

| Asset | Primary risks |
| --- | --- |
| User, Project and Workspace data | disclosure, modification, cross-project access |
| Source code and files | traversal, overwrite, exfiltration, malicious modification |
| Secrets and credentials | logging, prompt leakage, provider leakage, theft, replay |
| Canonical database, state and Events | tampering, forged transitions, rollback, replay |
| Task, Run and Approval integrity | forged authority, mismatched Approval, duplicate execution |
| Agent and AgentTeam definitions | capability escalation, model/prompt tampering |
| Model/tool configuration | provider redirection, unsafe capability expansion |
| Worker and Node access | impersonation, unauthorized dispatch, secret theft |
| Artifacts, results and provenance | tampering, malicious payloads, false provenance |
| Administrative privileges | escalation, session theft, confused-deputy behavior |
| Plugins and extensions | supply-chain compromise, permission escalation |
| Connector/browser/repository sessions | token/cookie leakage, session confusion, external side effects |
| Backup/update/import packages | tampering, rollback, malicious payloads, secret inclusion |

## 4. Actors and attacker capabilities

Relevant actors include:

- authenticated users with legitimate but limited access;
- platform administrators;
- service identities and Workers;
- model providers and local/self-hosted models;
- tool, MCP and capability providers;
- adapters and plugins;
- external Connector services and webhook senders;
- upstream package/repository maintainers;
- unauthenticated network clients where an endpoint is exposed;
- an attacker controlling Task text, files, retrieved knowledge, webpage content, tool output or downloaded Artifacts;
- a compromised Worker, plugin, provider, model endpoint, Connector, dependency or upstream release.

Assume an attacker may attempt prompt injection, schema abuse, path traversal, symlink escape, replay, forged metadata, identity confusion, SSRF, credential exfiltration, excessive resource use and supply-chain substitution.

## 5. Canonical security invariants

These rules are binding baseline architecture requirements:

1. **Model and Agent output is untrusted input to privileged systems.**
2. **Authority comes from canonical identity, policy and Approval decisions, never from an LLM request or natural-language claim.**
3. **Sensitive operations pass canonical backend enforcement points and cannot be authorized only in the frontend.**
4. **Canonical Workspace/file boundaries cannot be bypassed through provider-private paths.**
5. **Plaintext secrets are excluded from normal canonical serialization, logs, Events, traces, diagnostics and exports.**
6. **Backend, plugin, provider, Node or Worker identifiers do not confer authorization.**
7. **External content, retrieved knowledge and webpage/tool output cannot silently elevate permissions.**
8. **Optional adapters/plugins are not trusted merely because they are installed.**
9. **Single-node operation uses the same security ownership model as distributed mode.**
10. **Security-critical actions remain auditable through canonical actor/resource/action context.**

If an implementation appears to require violating one of these invariants, the work must stop for explicit architecture/security review and an ADR where the architecture materially changes.

## 6. Trust boundaries

The initial trust boundaries are:

1. client/browser/CLI -> Control Plane;
2. authenticated actor -> authorization/policy boundary;
3. user/Task/external content -> Agent/model context;
4. model output -> canonical capability/tool invocation;
5. capability/tool provider -> Executor or external side effect;
6. Executor -> Workspace/filesystem/network/host resources;
7. platform core -> adapter/plugin implementation;
8. Control Plane -> local or remote Worker;
9. platform -> model provider;
10. platform -> MCP/tool provider;
11. external webhook/Event -> Automation;
12. Connector/browser/repository content -> Agent/Task context;
13. upstream/update/package -> installed platform code/configuration;
14. backup/import/export package -> canonical platform state/files.

### Structured data-flow view

```mermaid
flowchart LR
    C[Client / CLI / Browser] -->|untrusted request| CP[Control Plane]
    CP --> AUTH[Identity / Policy / Approval]
    CP --> K[Canonical Task/Run Kernel]
    K --> A[Agent / Orchestrator]
    A -->|untrusted model output| CAP[Capability Enforcement]
    CAP --> TOOL[Tool / MCP Provider]
    CAP --> EX[Executor]
    EX --> WS[Workspace / Filesystem]
    EX --> NET[Network Boundary]
    CP --> W[Worker / Node]
    A --> M[Model Provider]
    EXT[Webhook / Connector / Browser / Repo] -->|untrusted external content| CP
    UP[Upstream / Update / Package] -->|supply-chain boundary| INST[Installed Code / Config]
```

No arrow implies authorization. Each privileged transition must still satisfy the relevant canonical policy and enforcement decision.

## 7. External entry points

Current or future entry points include:

- Control Plane HTTP/API requests;
- CLI and web-client requests through canonical APIs;
- Task goals, prompts and uploaded files;
- model responses;
- MCP/tool/provider responses;
- Worker registration, dispatch callbacks and results;
- webhook and Connector Events;
- browser/retrieval/repository content;
- plugin manifests and plugin code;
- import packages, backup restores and registry packages;
- upstream dependency and platform updates;
- configuration/environment input.

All external entry points require schema/structural validation, bounded resource handling and explicit trust classification before privileged use.

## 8. Privileged actions

Examples include:

- filesystem read/write/delete outside ephemeral in-memory state;
- command/process execution;
- network access and browser navigation;
- sending messages or mutating external services;
- repository writes, commits, pushes or merges;
- reading or delivering secret material;
- changing identity, authorization, Approval or policy state;
- registering or trusting a Worker/plugin/provider;
- installing or updating code/packages;
- importing/restoring canonical state;
- administrative configuration changes;
- destructive Task/Run/Workspace operations.

Sensitive actions should be deny-by-default unless canonical policy explicitly allows them, and should require Approval when policy or capability classification says Approval is required.

## 9. Baseline controls

### 9.1 Canonical security context

Security-critical decisions should carry canonical actor, action, resource, Project/Workspace and correlation context. Provider-private metadata may be retained for diagnostics but must not grant authority.

The initial reusable type is `ai_multi_agent_platform.security.SecurityContext`.

### 9.2 Secure-default decisions

The baseline decision model is explicit `ALLOW`, `DENY` or `REQUIRE_APPROVAL`. `baseline_decision(...)` is deny-by-default and deliberately ignores adapter metadata. Issue #15 owns the final authorization/Approval engine and must preserve or deliberately supersede these semantics through an explicit architecture decision.

### 9.3 Input validation

Untrusted inputs must be validated at the nearest canonical boundary. Endpoint/capability schemas remain required for semantic validation. The reusable `validate_untrusted_json(...)` helper provides baseline rejection of non-JSON objects, non-finite numbers and excessive nesting/item/string sizes.

### 9.4 Workspace and path confinement

Filesystem operations must:

- use platform-selected Workspace roots;
- reject absolute paths where a relative path is required;
- reject parent traversal;
- normalize/resolve paths before access;
- reject symlink/junction-resolved escapes;
- avoid provider-private path bypasses;
- use least-privilege OS/container permissions for production execution;
- treat TOCTOU races as a production sandbox concern, not as solved solely by string/path validation.

`resolve_within(...)` is the reusable baseline helper. The existing ReferenceExecutor is regression-tested against traversal and symlink escape.

### 9.5 Secrets and redaction

Plaintext secret material must not be part of normal canonical objects. Scoped secret references should be used instead of embedded values. Centralized redaction must run before logs, Events, traces, diagnostics, API display, prompts where applicable, evaluation Artifacts and exports can expose sensitive values.

**Contract ownership:** issue #34 owns the canonical `SecretReference`, secret-provider boundary and reusable redaction implementation. Issue #43 defines the security invariant and leak surfaces but intentionally does not create a competing secret contract while #34 is active. Downstream subsystems must consume the #34 contract once merged rather than defining provider-private alternatives.

### 9.6 Network and SSRF hooks

Future network/browser/Connector implementations must have a policy seam before performing outbound requests. At minimum support:

- allow/deny destination policy;
- scheme restrictions;
- DNS/IP re-checks where appropriate;
- blocking loopback, link-local, metadata-service and private ranges unless explicitly authorized;
- redirect validation;
- response/download size limits;
- timeout and concurrency limits;
- explicit classification of external side effects;
- cookie/session isolation per provider/user/Project scope.

Issue #74 extends these requirements for browser/web content.

### 9.7 Replay and deduplication hooks

External/distributed messages must carry stable identities/idempotency keys where repeated delivery can cause side effects. Consumers must distinguish duplicate delivery from a new authorized action. Issue #35 owns transport semantics; #14/#36 extend Worker identity and dispatch security; #44 extends webhook/Connector verification.

### 9.8 Resource limits

Security-sensitive boundaries should expose time, size, concurrency and rate-limit hooks. Unbounded model/tool/Executor/network payloads are not acceptable secure defaults.

### 9.9 Audit Events

Security-critical actions should produce an audit/security Event with canonical actor/action/resource context, decision, reason, correlation data and side-effect classification. Plaintext secrets must be excluded before persistence/telemetry.

The initial reusable `SecurityAuditEvent` defines this minimal security-owned shape. Observability may transport/serialize it later but must not redefine authorization semantics.

### 9.10 Supply-chain provenance

Dependencies, vendored/forked/ported source and architecture-significant upstreams must follow `LICENSE_POLICY.md`, `docs/UPSTREAMS.md`, the adoption checklist and update workflow. Security review must also consider integrity, pinning, source provenance, permissions and update rollback.

## 10. Threat categories and abuse cases

### Prompt and tool abuse

**Threats**

- prompt injection attempts to trigger unauthorized tools;
- retrieved/web/tool content asks the Agent to override system policy;
- an Agent fabricates Approval or identity claims;
- malicious model/tool output influences a later privileged operation;
- an attacker hides instructions in files or retrieved knowledge.

**Required mitigations**

- treat all model/external content as data, not authority;
- authorize the concrete canonical action after model planning;
- bind Approval to the exact reviewed actor/resource/action/invocation;
- revalidate tool input at invocation time;
- do not infer permissions from free-form text or adapter metadata;
- keep sensitive capabilities deny-by-default.

**Residual risk**

A sufficiently capable model may still be manipulated into proposing harmful but apparently plausible actions. Protection comes from enforcement, least privilege and Approvals, not model obedience alone.

### Execution and filesystem

**Threats**

- arbitrary command execution outside an allowed capability;
- Workspace traversal or symlink/junction escape;
- host filesystem leakage;
- environment-variable and secret leakage;
- unsafe temporary files/Artifacts;
- TOCTOU path races;
- resource exhaustion.

**Required mitigations**

- explicit capability allow-listing;
- Workspace confinement and symlink-aware resolution;
- filtered environment/secret delivery;
- per-Run time/resource limits;
- least-privilege process/container identity;
- production sandbox/container boundary where risk warrants it;
- Artifacts treated as untrusted on later consumption.

### Network, browser and Connectors

**Threats**

- SSRF into loopback/private networks/cloud metadata services;
- unauthorized outbound requests;
- malicious redirects/downloads/uploads;
- side effects triggered without Approval;
- cookie/token/session confusion;
- webhook spoofing/replay;
- malicious webpage/Connector content used as instructions.

**Required mitigations**

- outbound network policy hooks and destination validation;
- explicit side-effect classification;
- isolated sessions/cookies/tokens;
- signature/nonce/timestamp verification for webhooks where supported;
- replay/deduplication;
- download/content limits and safe Artifact handling;
- external content remains untrusted after retrieval.

### Authentication and authorization

**Threats**

- privilege escalation;
- cross-Project/resource access;
- service/Worker impersonation;
- stale sessions after revocation;
- direct adapter calls bypassing canonical policy;
- Approval replay against a different action.

**Required mitigations for #15/#36**

- canonical identity with revocation semantics;
- authorization at backend enforcement points;
- exact resource/action/Project scope;
- no authorization by backend identifier;
- service and Worker authentication distinct from user identity;
- immutable Approval binding to the reviewed action/invocation;
- denial remains effective when optional adapters are absent.

### Secrets and data leakage

**Leak paths**

- logs/traces/errors;
- prompts/model requests;
- Events/messages;
- exports/imports;
- Artifacts;
- plugin/tool payloads;
- backups;
- diagnostics.

**Required mitigations**

- canonical secret references from #34 rather than plaintext canonical storage;
- scoped secret delivery only to the authorized operation;
- centralized classification/redaction;
- avoid broad environment inheritance;
- retention/minimization rules for telemetry and Artifacts;
- backups/exports require explicit secret policy.

### Plugins, dependencies and upstream supply chain

**Threats**

- malicious plugin code or manifest;
- dependency compromise;
- upstream drift or ownership change;
- tampered package/update;
- permission escalation through manifest changes;
- provenance/license changes.

**Required mitigations**

- plugin installation does not imply trust;
- declared/approved permissions are canonical and reviewable;
- pinned/verifiable dependencies and packages where feasible;
- provenance records and review dates;
- explicit update workflow and rollback;
- no silent upstream replacement of canonical contracts.

### Distributed Workers

**Threats**

- Worker/service impersonation;
- replayed dispatch;
- capability misrepresentation;
- compromised/lost Worker;
- over-broad secret delivery;
- malicious/fabricated results.

**Required model for #14/#36**

- canonical Worker/service identity;
- authenticated registration and dispatch;
- replay-resistant job semantics;
- explicit trust level/capabilities;
- least-privilege scoped secrets;
- revocation/quarantine for compromised Workers;
- Worker results validated and treated according to trust/policy before privileged reuse.

## 11. Residual risks

The baseline does not claim to eliminate:

- kernel/OS/container escape vulnerabilities;
- compromised dependencies or compilers before stronger supply-chain verification exists;
- TOCTOU filesystem races in hostile multi-process environments;
- model-provider retention or misuse when a deployment intentionally sends data to an external provider;
- deployment-specific firewall/TLS/host-hardening failures;
- social engineering of humans granting Approvals;
- unknown vulnerabilities in future plugins, browsers, Connectors or imported packages.

High-risk deployments should use independent security review and penetration testing.

## 12. Assumptions

Current assumptions include:

- the host running the trusted Control Plane is not already fully compromised;
- canonical persistence is protected by deployment-level filesystem/database permissions;
- Python/runtime dependencies are obtained from intended sources;
- the ReferenceExecutor is a deterministic development/reference executor, not a complete hostile-code sandbox;
- later authentication/authorization, secret, Worker and network subsystems will extend rather than bypass this baseline.

## 13. Re-evaluation triggers

Review this threat model when any of the following changes:

- new privileged capability or execution primitive;
- remote Worker/Node support;
- authentication, authorization or Approval semantics;
- secret/config handling;
- transport/Event bus or webhook ingestion;
- Workspace/materialization behavior;
- plugin system or executable extension loading;
- browser/network access;
- Connector/repository write access;
- import/export/backup package formats;
- package registry/distribution;
- update/release/upstream workflow;
- new persistence boundary or multi-tenant deployment model;
- discovered vulnerability, near miss or security regression.

## 14. Required downstream extensions

The following issues must revisit this document and add subsystem-specific tests/controls:

- #12 capability/tool invocation threats;
- #14 remote Worker/Node threats;
- #15 authorization/Approval enforcement;
- #20 plugin isolation and supply chain;
- #34 secret/config handling and canonical redaction;
- #35 transport/replay/message security;
- #36 authentication/session/service identity;
- #37 Workspace/materialization isolation;
- #42 update/upstream integrity;
- #44 Connectors/external Events;
- #74 browser/network/web-content threats;
- #79 import/export package threats;
- #81 registry/distribution trust;
- #82 repository/Git side effects;
- #46 end-to-end conformance of accumulated security invariants.

Use [`SECURITY_EXTENSION_CHECKLIST.md`](SECURITY_EXTENSION_CHECKLIST.md) for every security-sensitive subsystem.

## 15. Initial regression ownership

`tests/test_security_baseline.py` establishes regression fixtures for currently implementable invariants:

- traversal and absolute-path rejection;
- symlink-resolved Workspace/Artifact escape;
- malformed/non-finite/unbounded input rejection;
- deny-by-default decisions;
- adapter-private metadata cannot grant authority;
- optional adapter absence does not alter canonical security ownership.

Secret serialization/redaction regression belongs to #34's canonical secret/config implementation and must be integrated into this threat model after that subsystem merges.

Future subsystem tests should extend this baseline rather than create separate, incompatible security rules.
