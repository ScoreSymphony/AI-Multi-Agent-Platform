# MCP Tasks (SEP-2663) interoperability

Issue: #964

## Decision

The platform supports **opt-in experimental interoperability** with the finalized MCP Tasks
extension while retaining exactly one canonical lifecycle authority.

```text
canonical Task
    -> canonical Run
        -> CapabilityInvocation / canonical ToolInvocation
            -> MCP provider adapter
                -> external MCP task binding
                    -> provider-native MCP task
```

An MCP task is never a platform Task or Run. Its handle, status and timestamps are external
provider state used to finish one already-authorized canonical capability attempt.

Recommendation: **`experimental_only`** until the pinned official Python SDK path implements the
finalized Tasks extension and upstream conformance coverage exists for the same wire profile. The
ordinary SDK-backed MCP path remains the stable synchronous default.

## Pinned upstream profile

This implementation is intentionally pinned to the current extension design rather than the older
experimental Tasks surface.

| Item | Pin |
| --- | --- |
| SEP | `SEP-2663` — Tasks Extension — Final |
| Extension identifier | `io.modelcontextprotocol/tasks` |
| MCP/stateless protocol family | `2026-07-28` |
| Platform optional Python SDK | `mcp==2.1.1` |
| Platform Tasks transport | `MCPStatelessHTTPClient` |

Normative/evaluation sources:

- <https://tasks.extensions.modelcontextprotocol.io/seps/2663-tasks-extension>
- <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- <https://github.com/modelcontextprotocol/python-sdk/blob/main/ROADMAP.md>

The `2025-11-25` experimental Tasks feature is explicitly **not** treated as wire-compatible with
this extension. In particular, this implementation does not use the legacy `task` parameter,
`tasks/result`, `tasks/list`, or legacy method-level task capability declarations.

At the time of #964 implementation, the repository-pinned official Python SDK line does not provide
client support for the finalized `io.modelcontextprotocol/tasks` extension. Tasks therefore live
only behind the existing experimental `2026-07-28` stateless HTTP compatibility client. No MCP
Tasks code is imported by the platform core or required by baseline/native capability invocation.

## Negotiation and wire behavior

The client first inspects `server/discover` and requires the server capability declaration:

```json
{
  "capabilities": {
    "extensions": {
      "io.modelcontextprotocol/tasks": {}
    }
  }
}
```

Only when both the platform configuration (`enable_tasks=true`) and server declaration are present
does a new `tools/call` advertise the extension in per-request client capabilities. The server
remains the sole decider whether that request completes synchronously or returns
`resultType: "task"`.

If the server does not advertise Tasks **and the canonical invocation has no existing durable task
binding**, the invocation uses the unchanged synchronous MCP path and does not declare the
extension. An existing binding is resolved first: recovery continues against that exact external
handle, and inability to poll it fails closed rather than redispatching a synchronous `tools/call`.
A server that requires or returns Tasks without negotiated capability on a new invocation is
therefore not silently accepted.

For task lifecycle requests the client implements:

- `tasks/get` for polling and terminal result retrieval;
- `tasks/update` for governed responses to `input_required` requests;
- `tasks/cancel` as a cooperative, acknowledgement-only cancellation signal.

The experimental stateless transport follows the `2026-07-28` Streamable HTTP routing envelope for
this profile. Every POST sends `MCP-Protocol-Version`, mirrors the JSON-RPC method into `Mcp-Method`,
and advertises both `application/json` and `text/event-stream` response types. `tools/call` mirrors
its tool name into `Mcp-Name`; `tasks/get`, `tasks/update` and `tasks/cancel` mirror the exact MCP task
ID there as required by SEP-2663. Values that are not safe plain-ASCII header values, including
leading/trailing whitespace or literal Base64-sentinel-shaped values, use the protocol's
`=?base64?...?=` UTF-8 encoding rather than being inserted raw.

Request-scoped SSE responses are accepted: progress/log notifications preceding the final JSON-RPC
response are validated as notification-shaped messages and do not become canonical lifecycle
transitions. The current compatibility path does not project those transient SSE notification
payloads into a separate platform progress stream; durable external status/observability for #964 is
provided by task polling and binding evidence. Optional `notifications/tasks` subscriptions are not
required for lifecycle correctness and are not yet claimed as supported.

`CreateTaskResult` is `Result & Task`, not `DetailedTask`. Its embedded seed may therefore already
report `completed`, `failed` or `input_required` without the status-specific payload that is required
on `tasks/get`. The client resolves any such non-working seed through `tasks/get` before handing it
to the provider lifecycle logic. This avoids both draft-era assumptions and fabricated result/error
payloads.

## Canonical-to-external binding

`MCPTaskBinding` stores the minimum state needed to recover one external task without giving it
canonical authority:

- MCP provider/server identity;
- canonical capability invocation ID;
- canonical Task ID;
- canonical Run ID;
- actor owner type/ID and Project scope;
- canonical causation identity where present;
- canonical correlation ID and idempotency key where present;
- raw external MCP task ID in the protected adapter store;
- external creation and latest-observation timestamps;
- latest provider-native status;
- protocol revision and extension identifier;
- polling metadata;
- durable keys for already-answered `inputRequests`;
- cancellation request/ack/error evidence;
- digest of terminal result/error payload rather than the payload itself.

The reference stores are:

- `InMemoryMCPTaskBindingStore` for deterministic local tests;
- `SqliteMCPTaskBindingStore` for restart-safe single-node recovery.

The SQLite store has unique constraints for `(provider_id, invocation_id)` and
`(provider_id, external_task_id)`. Rediscovering a handle therefore resumes the same canonical
attempt instead of creating a second Run or rebinding a task to another invocation.

The store also owns a durable pre-dispatch claim keyed by `(provider_id, invocation_id)`. The claim
is acquired **before** task-capable `tools/call` may create external work, so two provider instances
or processes sharing the same store cannot both dispatch the same canonical attempt. A successful
external task bind consumes the claim in the same persistence transaction. If delivery is ambiguous,
the process crashes before a handle is persisted, or a task-capable call completes synchronously,
the claim intentionally remains as a tombstone for that exact attempt. A later replay then fails
closed instead of silently repeating an external side effect. A distinct canonical retry/new Run
uses a distinct invocation identity and may dispatch normally.

All canonical task/run/owner scope required for a task binding is validated before the dispatch claim
and before external `tools/call`. Invalid canonical context therefore cannot create an orphaned MCP
task and only fail later during binding.

### Raw task IDs and observability

SEP-2663 permits task IDs to behave like bearer tokens. The exact ID is therefore persisted only
where recovery needs it. Generic `AdapterMetadata` emits a SHA-256 digest plus server, extension,
protocol, status, correlation and cancellation evidence. Raw tool arguments, idempotency keys,
external result payloads and raw task IDs are not copied into ordinary invocation telemetry.

## Status mapping

Provider-native state remains diagnostic metadata; it is not added to the canonical lifecycle enum.

| MCP state | Adapter behavior | Canonical effect |
| --- | --- | --- |
| `working` | continue polling, respecting `pollIntervalMs` | invocation remains in its existing running attempt |
| `input_required` | call a configured governed input handler, then `tasks/update`; otherwise cancel and fail closed | no automatic authority/input grant |
| `completed` | extract the original tool result and return normal `ToolResult` | ordinary output schema, result and later Verification policy still apply |
| `failed` | raise canonical `BACKEND_ERROR` with task evidence | explicit provider failure; never success |
| `cancelled` | raise canonical `CANCELLED` error observation | provider state does not decide the platform Run disposition by itself |
| unknown/new value | reject as `INVALID_PROVIDER_RESPONSE` | fail closed |
| missing/expired task | normalize to `NOT_FOUND` with `mcp_task_lost=true` | distinguish task loss from explicit execution failure |

A completed task whose embedded `CallToolResult` has `isError: true` preserves the existing MCP
adapter behavior and becomes `BACKEND_ERROR`; `completed` in MCP means the task machinery finished,
not that the underlying tool result was semantically successful.

The durable binding is also the monotonic external-observation authority. If a poll carries an older
`lastUpdatedAt` than the stored observation, that poll is retained only as stale provider evidence
and cannot drive terminal handling. In particular, a delayed success observation cannot replace a
newer external terminal observation or mutate canonical Run lifecycle state.

## Authorization and input requests

Authorization/Approval remains upstream of `MCPToolProvider.invoke` in the normal
`CapabilityInvoker` pipeline. Enabling Tasks cannot grant a capability or bypass #15.

Every persisted binding records and re-validates the canonical Task, Run, owner, Project,
causation, correlation and idempotency context before recovery. Knowing an external task ID is
therefore insufficient to query or cancel it through the provider: public platform calls resolve the
binding from the authorized canonical invocation, not from caller-supplied arbitrary MCP task IDs.

`input_required` payloads are untrusted provider requests. The adapter never automatically answers
them. A deployment may provide a `task_input_handler` only when that handler supplies the normal
user/model trust and governance semantics. Without one, the provider requests cancellation of the
exact external task and fails closed instead of inventing input or approval.

SEP-2663 guarantees input-request keys are unique for the lifetime of a task and recommends clients
deduplicate them across repeated observations. The binding therefore persists successfully answered
keys. A repeated `input_required` poll cannot present the same request to the governed handler or
send the same response a second time after a successful `tasks/update`. Unknown response keys fail
closed and trigger cancellation of the exact bound task.

## Cancellation authority

Platform cancellation remains authoritative.

When the canonical invocation coroutine is cancelled (for example because the Run/Capability
pipeline has already recorded cancellation intent), the MCP provider:

1. ensures an already-started durable bind reaches its persistence boundary;
2. marks cancellation intent on the exact external-task binding;
3. sends `tasks/cancel` for that bound task only;
4. records whether the server acknowledged it or whether cancellation delivery failed;
5. re-raises cancellation so the canonical invoker applies its existing disposition.

If persistence itself fails while cancellation is pending, the persistence failure wins; the adapter
does not report a clean cancellation for a binding whose durable state could not be established.

SEP-2663 cancellation is cooperative and acknowledgement-only. A successful `tasks/cancel` response
is therefore evidence that the intent was received, **not** proof that external work stopped. The
server may still report another terminal state. This can never overwrite a terminal canonical Run
outside the normal Run/lifecycle authority.

## Recovery and reconnect

On restart, the provider looks up `(provider_id, capability_invocation_id)` before capability
negotiation or any new `tools/call`:

- existing binding -> validate canonical context and resume `tasks/get` on that exact handle;
- no binding and no dispatch claim -> negotiate Tasks and, when advertised, atomically claim this
  canonical attempt before one new task-capable `tools/call`; otherwise use synchronous fallback;
- no binding but an existing dispatch claim -> fail closed because delivery is still in flight or
  became ambiguous before a recoverable external handle was persisted; never redispatch that exact
  attempt automatically;
- existing binding whose server no longer advertises Tasks -> still reconcile the bound handle and
  fail closed if it cannot be polled; never issue a replacement synchronous `tools/call`;
- missing/expired bound task -> explicit `NOT_FOUND` / `mcp_task_lost` failure;
- conflicting binding/context -> fail closed;
- duplicate observations -> merge idempotently without creating a second canonical attempt;
- stale external observations -> cannot replace a newer persisted external observation.

A restart therefore does not create another canonical Run and does not deliberately create a second
external task for an already-known or ambiguously dispatched attempt. Answered input-request keys,
cancellation evidence and dispatch claims are durable, so reconnect does not silently repeat those
provider-side actions.

## Idempotency and ambiguous delivery

SEP-2663 does not define a client-supplied idempotency key for task creation. The platform still
persists its own canonical idempotency key in the binding so a recovered handle can be checked
against the exact attempt context, but that value is not presented as a server-side MCP guarantee.

Consequently the adapter **does not automatically retry a task-capable `tools/call` after ambiguous
transport failure**. If the server might have created work but the client never received the task
handle, blind redispatch could duplicate a side effect.

The durable dispatch claim makes that rule restart- and concurrency-safe. It is consumed before the
first task-capable `tools/call`; a known task binding replaces it, while an ambiguous/no-handle or
synchronous completion leaves a tombstone for that exact invocation. This deliberately prefers
fail-closed duplicate prevention over transparent replay when the protocol cannot prove that a
second dispatch is safe.

The safe distinction is:

- retry `tasks/get` / reconnect for a known durable external handle;
- resume a known binding after platform restart;
- do not silently redispatch an ambiguously delivered or already-consumed creation call within the
  same canonical attempt;
- an explicit platform retry/new Run is a new attempt and may create a distinct external task.

The existing protocol-version rejection retry is only taken after an explicit unsupported-version
response and therefore is not treated as an ambiguous successful task creation.

## Progress and observability

MCP progress/status is evidence, not lifecycle truth. The binding records the latest external status
and timestamp, while canonical `InvocationRecord` remains owned by #12/#16. Polling does not emit
synthetic canonical Task/Run state transitions. Terminal tool output returns through the existing
`ToolResult -> CapabilityInvocationResult` path and remains subject to ordinary schema validation,
artifact/result handling and any downstream Verification policy.

## Security properties

The #964 profile enforces these boundaries:

- no caller-facing API accepts an arbitrary external task ID for lookup/cancellation;
- exact canonical Task/Run/actor/Project/causation/correlation/idempotency context is checked on
  recovery;
- task-aware canonical scope is validated before external work can be dispatched;
- a durable pre-dispatch claim prevents concurrent/restarted provider instances from silently
  creating multiple external operations for one canonical attempt;
- one external handle cannot bind to two canonical invocations for the same provider;
- raw bearer-like task IDs and canonical idempotency keys are not written into generic telemetry;
- external result and input-request payloads remain untrusted;
- unknown provider statuses fail closed;
- a server without Tasks uses the ordinary synchronous path only when no durable task binding exists;
- a known durable task binding is always reconciled before fallback, preventing duplicate side
  effects after restart or transient discovery changes;
- removing/disabling MCP Tasks does not affect native capabilities or the canonical lifecycle.

## Support recommendation

`experimental_only`

The architecture and wire path are implemented and contract-tested against the finalized SEP-2663
shape, including negotiation, asynchronous completion, update/cancel, missing-task handling,
recovery, stale/duplicate observation handling, durable pre-dispatch idempotency, required modern
routing headers, request-scoped SSE final responses and synchronous fallback. It should remain an
explicit experimental adapter profile until at least one maintained upstream client SDK/conformance
line supports the same finalized extension and a real external implementation is included in
repeatable interoperability evidence. Promoting it earlier would overstate upstream compatibility
even though the platform-side authority boundary is already correct.
