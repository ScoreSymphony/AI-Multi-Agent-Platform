# Agent-Sandbox provider authorization review (#798)

Reviewed upstream: `agent-sandbox/agent-sandbox`  
Pinned revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`  
Review date: 2026-09-12

This review narrows #798 to provider-side object ownership and tenant authorization. It is source-level evidence only. It does not claim that a cross-tenant exploit has been demonstrated against a live deployment.

## Conclusion

The pinned provider must **not** be treated as a canonical multi-tenant authorization boundary for a shared Agent-Sandbox service.

The reviewed native management API authenticates requests globally, but several sandbox-addressed handlers resolve the target sandbox by name without checking that the authenticated request user owns that sandbox. The list path is different: it obtains the authenticated user and filters `controller.List(user)` for non-system callers.

For the platform this means:

- #15 remains authoritative for tenant and object ownership;
- untrusted callers must not receive direct provider API access;
- a shared provider endpoint requires platform-mediated ownership validation before every sandbox-addressed operation, or a stronger per-tenant deployment/credential boundary;
- provider tokens identify/authenticate callers but cannot, at the reviewed revision, be assumed to enforce sandbox object ownership on all native routes.

## Source-level route review

The pinned router registers the following native routes under the configured `APIBaseURL` (default `/api/v1`):

| Method | Route | Source-level ownership observation |
|---|---|---|
| `GET` | `/api/v1/sandbox` | `ListSandbox` obtains the authenticated user; non-system users are filtered through `controller.List(user)` |
| `GET` | `/api/v1/sandbox/{name}` | `GetSandbox` resolves `controller.Get(name)`; no authenticated-user ownership comparison is performed in the handler |
| `DELETE` | `/api/v1/sandbox/{name}` | `DelSandbox` calls `controller.DeleteWithReason(name, ...)`; no authenticated-user ownership comparison is performed in the handler |
| `POST` | `/api/v1/sandbox/pause/{name}` | resolves `controller.Get(name)` then pauses; no authenticated-user ownership comparison is performed in the handler |
| `POST` | `/api/v1/sandbox/resume/{name}` | resolves `controller.Get(name)` then resumes; no authenticated-user ownership comparison is performed in the handler |
| `GET` | `/api/v1/logs/sandbox/{name}` | sandbox-addressed log retrieval; reviewed handler does not establish caller ownership before access |
| `GET` | `/api/v1/sandbox/files/{name}` | resolves the sandbox by name before listing files; no authenticated-user ownership comparison is performed in the handler |
| `POST` | `/api/v1/sandbox/files/{name}/upload` | resolves the sandbox by name before upload; no authenticated-user ownership comparison is performed in the handler |
| `GET` | `/api/v1/sandbox/files/{name}/download` | resolves the sandbox by name before download; no authenticated-user ownership comparison is performed in the handler |
| `DELETE` | `/api/v1/sandbox/files/{name}` | resolves the sandbox by name before deletion; no authenticated-user ownership comparison is performed in the handler |
| `POST` | `/api/v1/terminal/sandbox/{name}` | sandbox-addressed command surface; must be treated as ownership-sensitive |
| `GET` | `/api/v1/terminal/sandbox/{name}/ws` | sandbox-addressed terminal stream; must be treated as ownership-sensitive |

The same HTTP server also exposes E2B-compatible and sandbox-router paths. These are separate surfaces and require their own cross-token tests; success on one route family is not evidence for another.

## Security interpretation

### Authentication is not object authorization

The reviewed middleware applies API-key authentication to the management/E2B route families. That establishes a caller identity in request context, but the object-addressed handlers above do not consistently consume that identity when choosing the target sandbox.

The presence of authentication therefore does not prove that token A cannot operate on a sandbox created by token B if A learns B's sandbox name or provider identifier.

### Identifier entropy is not an authorization control

Provider-generated identifiers may reduce accidental discovery, but #798 does not accept unguessability as a tenant-isolation boundary. A sandbox identifier can appear in logs, metrics, URLs, browser sessions, evidence, crash output or another compromised workload.

A profile claiming multi-tenant isolation must remain secure after the attacker is given the victim sandbox identifier.

### Shared system credentials are especially sensitive

A platform integration that sends every tenant through one broad provider credential would collapse provider-side tenant distinctions completely. If such a topology is used, all object authorization must be enforced before provider dispatch and provider API access must remain private to the platform control plane.

Using separate provider tokens per tenant is not sufficient by itself at this reviewed revision because object-addressed handlers still require live proof that token ownership is enforced on every relevant route.

## Required live authorization matrix

Create two disposable users/tokens and two sandboxes, `A` and `B`. Retain status codes and sanitized response classifications, never token values.

Every operation below must exercise both same-tenant and cross-tenant directions where applicable:

| Surface | Same tenant | Cross tenant |
|---|---|---|
| provider/E2B sandbox `GET` | A→A and B→B must succeed | A→B and B→A must be rejected |
| native sandbox `GET` | must succeed | must be rejected |
| native logs | must succeed | must be rejected |
| native file list/download | must succeed | must be rejected |
| native upload/delete file | must succeed in disposable fixture | must be rejected |
| terminal/command execution | must succeed in disposable fixture | must be rejected |
| terminal websocket/router/connect path | must succeed | must be rejected |
| pause | must succeed | must be rejected |
| resume | must succeed | must be rejected |
| snapshot/create/restore paths exposed by the evaluated profile | must succeed | must be rejected |
| delete | must succeed only for its own final disposable sandbox | must be rejected cross-tenant |

A test where same-tenant and cross-tenant calls are all rejected is **not** a passing ownership test. The evaluation gate already treats legitimate same-tenant success plus cross-tenant rejection as the required shape for its initial read-only probe.

## Platform integration consequence

Until the full matrix is retained as live evidence, the only defensible platform topology is:

```text
untrusted agent/user
        |
        v
platform #15 authorization + canonical object ownership
        |
        v
private AgentSandbox adapter/client
        |
        v
Agent-Sandbox provider API
```

Direct user/agent access to the provider API is outside the supported protected-profile design.

If the final live campaign demonstrates that the provider does not enforce object ownership on required routes, #798 can still consider Agent-Sandbox as an optional execution mechanism behind strict platform mediation, but **provider-native multi-tenancy must be classified unsupported**. If platform mediation cannot prevent confused-deputy/cross-tenant dispatch safely, the corresponding shared-provider profile must be rejected/deferred.

## Relationship to the broader #798 decision

This finding does not by itself choose the final `adopt`, `optional_provider_only` or `reject/defer` outcome. It does establish a non-negotiable decision constraint:

> Agent-Sandbox authentication at the reviewed revision must not be promoted to canonical tenant/object authorization without live per-operation ownership proof.

The final recommendation must combine this authorization evidence with runtime isolation, egress, credential handling, Workspace/Artifact semantics, browser integration, lifecycle behavior and representative VPS resource measurements.
