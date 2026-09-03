# Issue #12 Completion Audit

Issue: `Build capability registry and MCP tool adapter`

This audit records both the original #12 delivery and the remediation required after the issue was reopened by a full-wording verification audit. The original #84/#94 foundation remains valid; PR #120 addresses the three requirements that were still incomplete.

## Acceptance criteria

- [x] **Canonical Capability and Tool Invocation contracts exist.** `capabilities/types.py` owns backend-neutral capability/invocation/result/audit types; the provider-neutral `ToolInvocation` / `ToolResult` boundary remains under `contracts/types.py`.
- [x] **Capability registry can discover/list/resolve providers.** `CapabilityRegistry` supports provider registration/removal, static inventory, policy-aware discovery, health refresh, exact/compatible version resolution, permissions, worker capability filtering and explicit conflicts.
- [x] **At least one native deterministic tool uses the canonical invocation path.** `NativeEchoProvider` exposes `tool.echo` through `CapabilityInvoker`.
- [x] **At least one MCP tool uses the same canonical invocation path.** Fake-backed contract coverage remains, and `test_mcp_sdk_transport.py` launches a real MCP stdio server using the official SDK and invokes it through the same registry/invoker path.
- [x] **Input validation occurs before provider execution.** Draft 2020-12 validation runs before provider invocation; malformed input is rejected and audited.
- [x] **Permission/approval hooks are present.** Registry permission checks and policy/governance/approval hooks are implemented. Approval-required calls bind to canonical `tool_invocation_*` identity and are revalidated before execution.
- [x] **Tool calls are traceable to task/run/agent IDs.** `InvocationTrace` requires canonical Task, Run and Agent IDs; persistent invocation events retain those IDs plus project/correlation/causation metadata.
- [x] **Provider-specific/MCP types do not leak into canonical APIs.** MCP SDK code exists only under `adapters/mcp_sdk.py`; SDK/Pydantic values are normalized to canonical JSON.
- [x] **MCP can be removed/disabled without breaking core/native-tool tests.** The SDK is an optional `mcp` extra and the core architecture test forbids MCP imports outside the adapter package.
- [x] **Unsupported/unavailable capabilities fail with clear canonical errors.** Registry/provider failures map to `ContractError` / `ErrorCode` categories.

## Reopened verification gaps

### 1. Policy-aware capability discovery

- [x] `CapabilityDiscoveryRequest` carries caller/scope `OperationContext`, granted permissions and worker capability context.
- [x] `CapabilityRegistry.discover_capabilities()` accepts a replaceable `CapabilityDiscoveryPolicyHook`.
- [x] `DENY` removes a capability from caller-visible usable discovery.
- [x] `ALLOW` remains visible.
- [x] `REQUIRE_APPROVAL` remains visible because the capability is usable through the canonical approval path.
- [x] Discovery works without a concrete #15 authorization backend; #12 defines the integration seam only.
- [x] Invocation remains the authoritative enforcement point and evaluates policy again before governance/provider execution. Discovery is never treated as a cached authorization grant.
- [x] The existing synchronous `list_capabilities()` remains explicitly documented as a static administrative/inventory view and does not claim policy authorization.

### 2. Canonical version/feature compatibility

- [x] `CapabilityCompatibilityRequest` represents minimum/maximum version bounds, bound inclusivity and required canonical features.
- [x] `CapabilitySpec.features` publishes provider-neutral feature compatibility metadata.
- [x] Compatible resolution filters features/ranges and deterministically selects the highest compatible version.
- [x] No compatible version returns canonical `UNSUPPORTED_CAPABILITY` with compatibility evidence.
- [x] Distinct labels that normalize to the same numeric compatibility version, such as `1.0` and `1.0.0`, fail with `CONFLICT` instead of being selected silently.
- [x] Opaque/non-numeric provider version labels remain available through exact version selection but are never silently ordered for compatibility/latest selection.
- [x] Exact `version` and `compatibility` requests are mutually exclusive.

The canonical compatibility baseline intentionally supports one-to-three-part dotted numeric versions. This keeps compatibility semantics deterministic and backend-neutral without pretending that arbitrary provider labels have a meaningful ordering.

### 3. Credential-requiring classification

- [x] `CredentialRequirement` is first-class canonical capability metadata with `none` / `required` classification.
- [x] Discovery and invocation policy hooks receive the `CapabilitySpec`, so credential requirement is available to policy evaluation.
- [x] Credential classification is separate from safety, side effects, permissions and approvals.
- [x] Capability contracts contain no secret value, secret reference, secret provider, vault type or concrete credential backend object.
- [x] Secret storage/resolution remains owned by the configuration/secrets and authorization boundaries (#34/#15).

## Required regression tests

Original required coverage remains in `tests/test_capabilities.py`, `tests/test_mcp_sdk_transport.py` and `tests/test_capability_observability.py`.

The reopened requirements are covered by `tests/test_issue_12_reopen.py`:

- [x] policy-denied capability is excluded while allowed/approval-gated capabilities remain visible;
- [x] policy-aware discovery works without a concrete #15 backend;
- [x] compatible version + feature selection succeeds deterministically and reaches invocation;
- [x] incompatible range fails with a canonical error;
- [x] ambiguous compatibility does not silently select a version;
- [x] opaque versions require exact selection when canonical compatibility cannot be inferred;
- [x] credential metadata survives registration, discovery policy and invocation policy evaluation;
- [x] credential classification does not introduce secret values or concrete secret-backend fields.

## MCP scope clarification

#12 requires MCP tools/resources to be discovered where they map to platform capabilities. The current provider contract is an invokable **tool** contract, so MCP tools map directly and are complete for this issue. MCP resources are not silently coerced into callable tools because doing so would create incorrect canonical semantics. A future resource/knowledge provider can project MCP resources behind the corresponding platform-owned contract without changing the tool capability API.

This is consistent with #12's non-goal of supporting every MCP extension immediately.

## Auditability, evidence and redaction

`InvocationRecord` retains timestamp, provider placement, error state and approval decision. `EventRepositoryInvocationObserver` persists each lifecycle record on a dedicated invocation stream through the canonical repository abstraction, including the SQLite durability baseline.

The observer intentionally does **not** persist raw arguments or raw output. This supplies the #12 redaction seam without prematurely implementing the full authorization/data-governance policy owned by later security work.

`ToolResult` and `CapabilityInvocationResult` support `result_ref`, `artifact_refs` and `evidence_refs`, and `CapabilityInvoker` propagates them rather than discarding them.

## External dependency decision

The official Model Context Protocol Python SDK is used only for the concrete optional transport:

- upstream: `modelcontextprotocol/python-sdk`;
- pinned version: `mcp==2.1.1`;
- license: MIT;
- boundary: `ai_multi_agent_platform.adapters.mcp_sdk`;
- baseline required: no;
- recurring paid service: no;
- adoption/exit review: `docs/upstream/MCP_PYTHON_SDK_ADOPTION.md`.

## Definition of Done

Agents request canonical capability IDs. The platform resolves those IDs to native or MCP implementations, validates inputs/outputs, applies replaceable policy/approval hooks, supports caller-context policy-aware discovery, resolves exact or canonical compatible versions without silent ambiguity, preserves canonical trace/governance identities, exposes credential requirement without secret-backend coupling, captures evidence references and can persist lifecycle audit events.

Changing or removing MCP, the future authorization backend or the future secrets backend does not require changing agent/task contracts.

## Current quality gate for PR #120

- [x] `ruff format --check .`
- [x] `ruff check .`
- [x] `mypy`
- [x] full `pytest` including native and MCP transport coverage
- [x] `python -m build`
- [x] merge-ready against current `main`

The merge-ready PR #120 CI passed with **277 tests**. Post-merge `main` CI is verified operationally after the merge rather than being pre-declared by this branch-local audit.
