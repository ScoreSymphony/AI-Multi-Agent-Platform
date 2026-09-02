# Issue #12 Completion Audit

Issue: `Build capability registry and MCP tool adapter`

This audit is the close-out check for #12. It evaluates the issue against the merged #84 foundation plus the #94 completion slice. The issue must remain open until the final merge-ready CI is green.

## Acceptance criteria

- [x] **Canonical Capability and Tool Invocation contracts exist.** `capabilities/types.py` owns backend-neutral capability/invocation/result/audit types; the provider-neutral `ToolInvocation` / `ToolResult` boundary remains under `contracts/types.py`.
- [x] **Capability registry can discover/list/resolve providers.** `CapabilityRegistry` supports provider registration/removal, discovery, health refresh, version resolution, permissions, worker capability filtering and explicit conflicts.
- [x] **At least one native deterministic tool uses the canonical invocation path.** `NativeEchoProvider` exposes `tool.echo` through `CapabilityInvoker`.
- [x] **At least one MCP tool uses the same canonical invocation path.** Fake-backed contract coverage remains, and `test_mcp_sdk_transport.py` now launches a real MCP stdio server using the official SDK and invokes it through the same registry/invoker path.
- [x] **Input validation occurs before provider execution.** Draft 2020-12 validation runs before provider invocation; malformed input is rejected and audited.
- [x] **Permission/approval hooks are present.** Registry permission checks and policy/governance/approval hooks are implemented. Approval-required calls bind to canonical `tool_invocation_*` identity and are revalidated before execution.
- [x] **Tool calls are traceable to task/run/agent IDs.** `InvocationTrace` requires canonical Task, Run and Agent IDs; persistent invocation events retain those IDs plus project/correlation/causation metadata.
- [x] **Provider-specific/MCP types do not leak into canonical APIs.** MCP SDK code exists only under `adapters/mcp_sdk.py`; SDK/Pydantic values are normalized to canonical JSON.
- [x] **MCP can be removed/disabled without breaking core/native-tool tests.** The SDK is an optional `mcp` extra and the existing core architecture test forbids `mcp` imports outside the adapter package.
- [x] **Unsupported/unavailable capabilities fail with clear canonical errors.** Registry/provider failures map to `ContractError` / `ErrorCode` categories.

## Required tests

- [x] native tool success — `tests/test_capabilities.py`;
- [x] MCP tool success — fake-backed contract test plus real stdio integration in `tests/test_mcp_sdk_transport.py`;
- [x] invalid input schema — `tests/test_capabilities.py`;
- [x] unavailable provider — `tests/test_capabilities.py`;
- [x] timeout/cancellation — `tests/test_capabilities.py`;
- [x] permission denied — `tests/test_capabilities.py`;
- [x] approval required — `tests/test_capabilities.py`;
- [x] provider error mapping — `tests/test_capabilities.py`;
- [x] duplicate capability registration — `tests/test_capabilities.py`;
- [x] capability version mismatch — `tests/test_capabilities.py`;
- [x] trace/correlation preservation — `tests/test_capabilities.py` and durable event coverage in `tests/test_capability_observability.py`.

## Deliverables

- [x] Capability domain/contract definitions.
- [x] Capability registry.
- [x] Tool provider/invocation interfaces.
- [x] Native deterministic reference capability.
- [x] MCP adapter.
- [x] Concrete optional MCP stdio/Streamable HTTP transport.
- [x] Permission/approval hooks.
- [x] Native/MCP contract and integration coverage.
- [x] Example configuration/documentation in `docs/CAPABILITIES.md`.
- [x] Durable invocation observer using the existing `EventRepository`.
- [x] Result/artifact/evidence references survive the canonical provider path.
- [x] Upstream provenance/adoption review for both `jsonschema` and the optional MCP SDK.

## MCP scope clarification

#12 requires MCP tools/resources to be discovered where they map to platform capabilities. The current provider contract is an invokable **tool** contract, so MCP tools map directly and are complete for this issue. MCP resources are not silently coerced into callable tools because doing so would create incorrect canonical semantics. A future resource/knowledge provider can project MCP resources behind the corresponding platform-owned contract without changing the tool capability API.

This is consistent with #12's non-goal of supporting every MCP extension immediately.

## Auditability, evidence and redaction

`InvocationRecord` now retains timestamp, provider placement, error state and approval decision. `EventRepositoryInvocationObserver` persists each lifecycle record on a dedicated invocation stream through the canonical repository abstraction, including the SQLite durability baseline.

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

Agents request canonical capability IDs. The platform resolves those IDs to native or MCP implementations, validates inputs/outputs, applies policy/approval hooks, preserves canonical trace/governance identities, captures evidence references and can persist lifecycle audit events. Changing or removing the MCP transport does not require changes to agent/task contracts.

The functional Definition of Done and the final repository quality gate are both satisfied.

## Final quality gate

- [x] `ruff format --check .`
- [x] `ruff check .`
- [x] `mypy`
- [x] full `pytest`
- [x] `python -m build`
- [x] merge-ready against current `main`
- [x] post-merge CI on `main`

Verified after the squash merge of PR #94: post-merge CI run `33686231713` passed on `main` with 204 tests passing.
