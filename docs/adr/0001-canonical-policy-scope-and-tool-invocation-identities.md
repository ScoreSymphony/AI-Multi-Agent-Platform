# ADR 0001: Canonical Policy Scope and Tool Invocation identities

- **Status:** Accepted
- **Date:** 2026-09-02
- **Issue:** #4

## Context

The canonical platform model must support model selection by capability/policy scope and approvals for one concrete sensitive tool call without adopting backend-private identifiers as platform identity.

The product and architecture principles require model assignments per agent, task, step or capability/policy scope. The provider-neutral tool contract also exposes an invocation handle, but a provider invocation ID such as `invoke-1` is not globally stable platform identity and cannot safely become an Approval/Event subject.

Issue #4 therefore needs stable canonical targets while leaving the final policy engine, tool transport and concrete provider implementations replaceable.

## Decision

### Policy Scope

Introduce the platform-owned `PolicyScope` support type with identity:

```text
policy_scope_<uuid>
```

`PolicyScope` is a canonical targeting primitive for model assignment. `ModelAssignment(subject_type="policy", ...)` keeps the public policy-scope spelling required by the architecture, but its `subject_id` must be a canonical `policy_scope_<uuid>`.

`policy` is **not** a generic canonical subject type. Generic domain subjects (for example Event) use the explicit canonical type `policy_scope`. This prevents an API alias from leaking into the canonical subject vocabulary.

`PolicyScope` does not define policy evaluation, authorization semantics or enforcement. Those remain separate later decisions.

### Tool Invocation

Introduce the platform-owned `ToolInvocation` support type with identity:

```text
tool_invocation_<uuid>
```

It references a canonical `tool_<uuid>` and supplies the stable governed-action identity used by Approval, Event and audit records.

Provider-neutral contract DTOs may continue to carry implementation/provider handles such as `invocation_id="invoke-1"` and `tool_ref="provider-tool-write"`. Those values do not become canonical IDs.

The explicit boundary function `contracts.domain_mapping.map_tool_invocation_to_domain(...)` maps a contract `ToolInvocation` to the canonical domain Tool Invocation. It requires the already-resolved canonical Tool ID and stores the contract invocation/tool handles as `ExternalRef` values. This creates a deterministic governance/audit link without coupling the domain model to a ToolProvider implementation.

A governed invocation must also be bound to the exact arguments that were approved. Provider Contract `2.0` therefore defensively deep-freezes `ToolInvocation.arguments` when the contract value is created. The mapping boundary computes a deterministic SHA-256 digest of that immutable JSON-compatible argument value and records the digest in canonical Tool Invocation provenance as `arguments_sha256`. The same contract object subsequently passed to a ToolProvider cannot be mutated in-place to execute different arguments under an already-approved canonical invocation identity.

Adapters that need a conventional JSON transport object use `ToolInvocation.arguments_json()`. It returns a detached recursive `dict`/`list` copy suitable for standard JSON encoding; mutations to that exported copy do not alter the governed snapshot or its digest.

Because Provider Contract `1.0` exposed mutable argument-dictionary semantics, the immutable behavior is a breaking contract change and is published as Provider Contract `2.0`. Providers relying on the new behavior must advertise `2.0` rather than silently changing `1.0` semantics.

### Event contract versioning

Event schema `1.0` retains its previously published compatibility rules. Strict canonical subject-type/ID enforcement is published as Event schema `2.0`. New canonical subject identities (`policy_scope`, `tool_invocation`) are part of the v2 vocabulary rather than silently narrowing v1.

## Consequences

### Positive

- model assignment can target policy scope without arbitrary backend IDs;
- one sensitive tool invocation can be approved/audited independently from the reusable Tool definition;
- provider invocation IDs remain replaceable external references;
- approved tool-call identity is cryptographically bound to an immutable argument snapshot;
- adapters retain a supported standard JSON export path through `arguments_json()`;
- ToolProvider implementations do not become lifecycle or identity authorities;
- Event v1 consumers are not broken by a silent contract narrowing;
- future adapters have one explicit mapping point between provider DTOs and canonical governance identity.

### Costs / constraints

- callers that need per-invocation governance must create/map a canonical Tool Invocation before creating the Approval/Event subject;
- callers must resolve the canonical Tool ID at the boundary;
- Provider Contract `2.0` Tool Invocation arguments are read-only after construction and callers must create a new invocation for changed arguments;
- adapters that serialize invocation arguments must use the detached `arguments_json()` export rather than serializing the internal frozen representation directly;
- Provider Contract `1.0` adapters require an explicit migration to `2.0` before claiming the immutable semantics;
- Policy Scope is an additional canonical scope identity that persistence/API work must eventually support;
- Event v1 and v2 coexist until migration policy retires v1.

## Alternatives considered

### Use provider invocation IDs directly

Rejected. Provider invocation handles are backend-private, may collide, may change with provider replacement and violate Issue #4 canonical-ID rules.

### Make the reusable Tool the Approval subject

Rejected for invocation-specific approvals because it over-broadly identifies a tool definition rather than the exact governed action.

### Keep invocation arguments mutable and approve only the invocation ID

Rejected because a caller could mutate the arguments after approval and execute a different action under the same approved identity. Identity-only approval is insufficient for action-level governance.

### Change ToolInvocation semantics while continuing to advertise Provider Contract 1.0

Rejected because existing `1.0` adapters could legitimately mutate or normalize the supplied argument dictionary. The normative contract-versioning rules require a new major version for that semantic change.

### Remove policy-scoped Model Assignment until authorization is implemented

Rejected because it contradicts the existing product and architecture principles and would unnecessarily couple model assignment to the timing of the final policy engine.

### Narrow Event schema 1.0 in place

Rejected because changing previously accepted subject values is a breaking contract change and therefore requires a new major schema version.

## Affected contracts and code

- `src/ai_multi_agent_platform/domain/models.py`
  - `PolicyScope`
  - canonical `ToolInvocation`
  - `ModelAssignment`
  - Approval/Event subject validation
  - deep-freeze handling of Enum values
- `src/ai_multi_agent_platform/contracts/types.py`
  - Provider Contract `2.0`
  - provider-neutral contract `ToolInvocation` deep-freezes invocation arguments
  - supported `arguments_json()` transport export
- `src/ai_multi_agent_platform/contracts/domain_mapping.py`
  - explicit contract-to-domain Tool Invocation mapping
  - deterministic `arguments_sha256` binding
- `docs/CONTRACTS.md`
  - Provider Contract `2.0` migration semantics
- `schemas/domain/common.schema.json`
  - `policyScopeId`
  - `toolInvocationId`
- `schemas/domain/event.schema.json`
  - backward-compatible Event v1
- `schemas/domain/event.v2.schema.json`
  - strict canonical Event v2 subjects
- Issue #4, PR #54 and its final follow-up PR
