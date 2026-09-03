# Authorization, Permissions and Approvals

Issue: #15

This document defines the platform-owned authorization boundary for the general-purpose
AI Multi-Agent Platform. Authentication (#36) may establish an identity later, but
authorization must not depend on one login provider, one deployment topology, one agent
framework, or one policy engine.

## 1. Security invariants

1. Authorization is evaluated by the platform, never by model output or prompt text.
2. Unknown principals/actions are denied by default.
3. The canonical decision vocabulary is `allow`, `deny`, `require_approval`.
4. Approval authorizes one immutable proposed action. A changed payload produces a new
   digest and cannot reuse the previous approval.
5. Raw provider objects are adapter plumbing. Agents/orchestrators must receive
   authorization-enforced platform paths rather than unrestricted provider handles.
6. Secret values are never part of authorization requests, approval records, or
   authorization audit records.
7. Provider-private identity is not canonical platform authority.
8. Project/workspace and other scope checks are evaluated server-side.
9. Every security decision preserves correlation context for audit/observability.
10. Approval/rejection itself is a privileged action and must pass authorization.
11. Multi-user/multi-tenant policy is supported by the contract, but authentication and
    advanced policy backends remain replaceable extensions.

## 2. Canonical actor model

Authorization distinguishes execution identity from ownership/scope.

| Actor type | Meaning |
| --- | --- |
| `human` | Human user established by a future authentication/session layer |
| `service` | Internal or external service identity |
| `agent` | Agent execution identity; never automatically inherits unrestricted user authority |
| `worker` | Node/worker service identity |
| `automation` | Scheduled/event-driven automation identity |
| `integration` | Connector/integration service identity |

Organization and team membership are scope/ownership context. They do not require the
platform to hard-code a particular directory or IAM product.

## 3. Resource scopes

The canonical vocabulary includes organization, team, project, workspace, task, run,
agent, agent team, capability, tool, file, artifact, memory, knowledge source,
model/provider configuration, node, worker, automation, connector/integration, secret
reference, plugin, and administrative settings.

Additional backends may use richer attributes through `trust_context` and security labels
without replacing these platform-owned concepts.

## 4. Actions

The platform vocabulary contains:

- `view`, `read`
- `create`, `modify`, `delete`
- `execute`
- `approve`
- `administer`
- `manage_integrations`
- `manage_credentials`
- `dispatch`
- `invoke_sensitive_capability`

Capabilities may map a normal side-effect-free call to `execute`; sensitive, restricted,
credential-bearing, external, or destructive capability calls use the sensitive action
where appropriate.

## 5. AuthorizationProvider contract

`AuthorizationRequest` contains:

- principal reference and actor type;
- action;
- resource type/reference;
- organization/team/project/workspace context;
- task/run/agent context;
- capability reference and side-effect classification;
- security labels;
- node/trust context;
- optional SHA-256 digest of the original northbound request payload;
- immutable proposed-action digest;
- optional approval ID;
- canonical `OperationContext` for correlation/causation/ownership.

`AuthorizationDecision` returns:

- outcome: `allow`, `deny`, or `require_approval`;
- human-readable reason;
- policy identifier;
- optional constraints/conditions;
- value-free audit metadata;
- namespaced adapter metadata.

The original `allowed=True/False` constructor remains accepted as a compatibility shim for
older issue-#5 fake/adapters, but new code should use the tri-state outcome.

## 6. Reference policy provider

`LocalAuthorizationProvider` is deliberately small and deterministic. It exists so the
platform is secure and testable without a paid IAM service.

A `LocalPrincipalPolicy` may constrain:

- actor type;
- explicit allowed actions;
- explicit approval-gated actions;
- resource types;
- project IDs;
- organization IDs;
- workspace IDs;
- administrator status.

Empty scope/resource sets mean no *additional* restriction; actions are still
deny-by-default. A missing principal policy is denied.

This provider is a reference implementation, not the long-term policy language. OPA,
Cedar, cloud IAM, directory-backed RBAC/ABAC, or another engine can be added behind the
same `AuthorizationProvider`.

## 7. Approval lifecycle and exact-action binding

The canonical domain `Approval` owns approval identity and lifecycle status. The security
layer adds `ApprovalRecord` binding metadata around that same entity rather than owning a
second approval ID/status model.

The binding record adds:

- requester;
- action/resource/capability;
- task/run context;
- immutable SHA-256 proposed-action digest;
- optional safe payload reference;
- risk classification and policy source;
- expiry timestamp;
- decision timestamp/comment.

Reference states reuse the canonical domain vocabulary:
`pending`, `approved`, `rejected`, `expired`, `cancelled`.
`rejected` is the canonical spelling of a denied approval request.

The digest includes actor, scope, action, resource, security labels/trust context and the
proposed payload. The payload itself is not stored in the approval record. File/knowledge
content is bound by cryptographic content digest. Secret create/rotate uses a process-local
HMAC binding so changing secret material changes the requested action without exposing the
plaintext or a reusable unsalted password hash.

An approved action is reusable only for the same digest and only until its expiry
timestamp. Pending requests expire deterministically when read after expiry.

`ApprovalService` is a lifecycle/storage primitive. Public direct calls to `decide()` or
`cancel()` are rejected. Application code must use `AuthorizationGate.decide_approval()`
or `AuthorizationGate.cancel_approval()` so the actor performing the approval decision is
itself authorized. Approval decisions never recursively create another approval: policy
must explicitly return `allow` for the canonical `approve` action.

## 8. AuthorizationGate

`AuthorizationGate` is the canonical enforcement service:

1. compute the exact proposed-action digest;
2. call the configured `AuthorizationProvider`;
3. on `allow`, continue;
4. on `deny`, stop with canonical `FORBIDDEN`;
5. on `require_approval`, resolve a still-valid exact-action approval or create/reuse a
   pending request;
6. emit a structured value-free audit record;
7. continue only after the exact approved action is presented/retried;
8. authorize approvers before mutating approval state.

The gate does not perform authentication and does not own a user database.

## 9. Anti-bypass enforcement points

The current repository exposes explicit server-side wrappers in
`security/enforced_providers.py`:

| Boundary | Wrapper | Protected operations |
| --- | --- | --- |
| Tool provider | `AuthorizedToolProvider` | direct tool invocation |
| Lifecycle backend | `AuthorizedLifecycleBackend` | start/read/cancel Run |
| File provider | `AuthorizedFileProvider` | read/write |
| Memory provider | `AuthorizedMemoryProvider` | get/put |
| Knowledge provider | `AuthorizedKnowledgeProvider` | index/query/get |
| Secret provider | `AuthorizedSecretProvider` | create/resolve/rotate/revoke/delete/metadata |

Issue #13's refined storage contracts are protected separately in
`security/data_enforcement.py`:

| Boundary | Wrapper | Protected refined operations |
| --- | --- | --- |
| File/artifact | `AuthorizedDataFileProvider` | create/get/list/stream/delete/checksum/link/orphan detection plus inherited read/write |
| Memory | `AuthorizedDataMemoryProvider` | write/get/query/search/supersede/delete/expire plus inherited get/put |
| Knowledge | `AuthorizedDataKnowledgeProvider` | register/ingest/status/search/reindex/remove plus inherited index/query/get |

The refined wrappers deliberately protect their inherited core methods too. A caller
cannot bypass policy by switching from a refined API method to the older provider method.

The issue-#12 capability registry keeps its canonical governance hooks.
`CapabilityAuthorizationBridge` maps capability metadata and invocation arguments to the
same #15 policy/approval gate. Restricted/sensitive, external, destructive, or
credential-bearing capabilities are mapped to the sensitive action.

The existing versioned v1 Control Plane predates the #15 action vocabulary and exposes
stable commands such as `task:start` and `project:create`. `ControlPlaneAuthorizationBridge`
is the migration boundary: it maps those northbound strings into canonical
`AuthorizationAction`/`ResourceType` values before policy evaluation, while keeping the
published v1 API stable. Mutating create commands hash the canonical JSON of the original
northbound request with SHA-256 and carry only that digest into authorization. Internal
enrichment, such as a platform-generated `task_id`, does not alter that binding. Retrying
the same approved request therefore resumes through the same gate, while any changed
northbound payload produces a different proposed-action digest and requires a new approval.

Future node/worker dispatch, connectors, automation, plugin management, and admin APIs
must use this same gate rather than inventing a second permission model.

## 10. Secret handling

Authorization for a secret uses only its safe canonical reference plus access metadata
(consumer, purpose, requested lifetime, task/run/capability scope). Plaintext secret
material never appears in `AuthorizationRequest`, `ApprovalRecord`, or
`AuthorizationAuditRecord`.

For create/rotate, a process-local HMAC fingerprint participates only in the proposed
action digest. It is not persisted as secret material and cannot be reused as a raw
password hash across process restarts. The plaintext value is passed to the underlying
secret backend only after authorization.

## 11. Audit model

`AuthorizationAuditRecord` captures:

- actor and actor type;
- action/resource;
- outcome/reason/policy;
- timestamp;
- correlation ID;
- project/task/run;
- approval ID;
- requested-action digest.

This is intentionally suitable for issue #16 observability sinks without making
observability the authority for policy.

## 12. Multi-user readiness

The contract is ready for later authentication and multi-tenant policy because actor
identity, organization/team/project/workspace scope, service identities, approval
authority and resource permissions are explicit. The local provider intentionally does
not pretend to be a complete enterprise IAM system.

Authentication (#36) should create/resolve principals and sessions, then feed the
canonical identity context into this authorization layer. It must not redefine the
authorization vocabulary.
