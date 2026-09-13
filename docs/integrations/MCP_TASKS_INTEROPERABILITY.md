# MCP Tasks (SEP-2663) interoperability

Issue: #964

## Decision

Adopt MCP Tasks as an **optional, experimental interoperability optimization** behind the
existing MCP capability adapter. Do not make MCP Tasks a canonical platform lifecycle and do
not require them for baseline MCP compatibility.

The canonical ownership relationship remains:

```text
canonical Task
  -> canonical Run
    -> canonical CapabilityInvocation / ToolInvocation
      -> external MCP task binding (adapter evidence only)
```

An MCP `taskId` must never become a platform `Task.id`, `Run.id`, lifecycle owner, approval
identity or verification identity. A server that does not implement Tasks continues to use the
ordinary synchronous `tools/call` path.

## Upstream pin and reviewed semantics

The implementation is reviewed against:

- extension: `io.modelcontextprotocol/tasks`;
- specification: SEP-2663, status `Final`;
- reviewed SEP revision:
  `9b44c6b4dcd2451bc49abd39e47eda36b396e8dd`;
- platform stateless MCP protocol revision: `2026-07-28`;
- supported task-augmented request at the reviewed revision: `tools/call`;
- task methods: `tasks/get`, `tasks/update`, `tasks/cancel`;
- provider task states: `working`, `input_required`, `completed`, `cancelled`, `failed`.

The reviewed SEP requires the client to declare the extension in per-request client
capabilities and the server to advertise the same extension from `server/discover`. A server
may still return an ordinary synchronous result after successful negotiation. The client does
not request asynchronous execution per call; the server decides whether to return a task.

The repository's stable official Python SDK remains pinned to `mcp==2.1.1`. That release does
not implement SEP-2663 Tasks. Therefore #964 does **not** replace the stable SDK adapter or
invent private SDK hooks. The Tasks prototype extends the already opt-in, platform-owned
stateless HTTP compatibility adapter. A future SDK version can replace that wire implementation
without changing canonical contracts or persisted application identity.

## Mapping to platform-owned concepts

| MCP concept | Platform treatment |
| --- | --- |
| `taskId` | External adapter identifier in `MCPTaskBinding` and `mcp.task` metadata only |
| `working` | Provider observation while the canonical capability invocation is still running |
| `input_required` | Provider observation; optionally satisfied through the invocation-scoped input handler and `tasks/update` |
| `completed` | The embedded original tool result is normalized to the ordinary `ToolResult` path |
| `failed` | Canonical provider `BACKEND_ERROR`; it does not set platform `Task`/`Run` state directly |
| `cancelled` | Canonical provider `CANCELLED` evidence; platform cancellation policy remains authoritative |
| `tasks/get` | Poll/reconciliation operation for an already-bound external handle |
| `tasks/update` | Scoped operation requiring the exact canonical invocation binding |
| `tasks/cancel` | Best-effort provider-side propagation after canonical cancellation/timeout intent |

No new `InvocationStatus`, canonical `Task` state or `Run` state is introduced.

## Binding model and identity

`MCPTaskBinding` is keyed by `(server_id, canonical invocation_id)` and stores only the fields
needed to reconcile external work:

- canonical invocation ID plus canonical task/run/agent correlation where available;
- MCP server ID and provider tool reference;
- external `taskId`;
- protocol and SEP revision evidence;
- idempotency key;
- last observed external status;
- cancellation request/acknowledgement evidence.

The binding deliberately does not persist task result payloads, input payloads, credentials or
other sensitive content. External task IDs remain namespaced provider evidence.

Exactly one external task handle may bind to one canonical invocation attempt. Reusing the same
canonical invocation resumes/polls the existing binding rather than issuing another `tools/call`.
A new canonical attempt uses a new invocation ID and may therefore receive a new external task.

The in-memory repository is suitable for deterministic tests. `SqliteMCPTaskBindingRepository`
provides single-node restart durability and offloads runtime SQLite operations from the event
loop. A restarted client can resume `tasks/get` directly from the persisted binding without
redispatching the original side-effecting tool call.

## Capability negotiation and fallback

The task-aware path first sends `server/discover` with:

```text
io.modelcontextprotocol/clientCapabilities.extensions.io.modelcontextprotocol/tasks = {}
```

If the server does not advertise the extension, the adapter performs the existing synchronous
`tools/call` without declaring Tasks support. A server returning `resultType: "task"` to that
non-negotiated call is rejected as an invalid provider response.

If a server advertises Tasks but returns an ordinary result, that result follows the normal MCP
tool path. If it returns a task result, the adapter persists the binding before polling it.
Unknown/future external task states fail closed rather than being guessed into canonical states.

## Completion and verification

`completed` is not trusted as platform completion. The embedded result is treated exactly like
an ordinary MCP tool response:

1. normalize it to provider-neutral JSON;
2. return it through `ToolResult`;
3. let the existing capability pipeline perform output validation, observation and canonical
   result handling;
4. let normal platform verification/review policy decide whether the containing work is
   acceptable.

MCP task completion therefore cannot bypass #86 verification or any later result acceptance
rule.

A tool-level `isError: true` inside a protocol-level `completed` task remains a tool/provider
error, matching the ordinary MCP adapter behavior.

## Cancellation and races

Canonical platform cancellation/timeout remains authoritative. When the in-flight invocation is
cancelled after an external binding exists, the adapter records provider-cancellation intent and
sends `tasks/cancel` for the task bound to that exact invocation.

The SEP cancellation acknowledgement is treated only as evidence. It does not mark the external
task, canonical Run or canonical Task as cancelled. Subsequent provider completion cannot
resurrect or overwrite a canonical terminal decision owned by the platform.

Explicit task cancellation and input update APIs accept `ToolInvocation`, not an arbitrary
external `taskId`. The binding scope is revalidated before any provider-side mutation. Knowledge
of a task ID therefore grants no authority over another invocation.

## Recovery and task loss

On restart, persisted bindings are reconciled with `tasks/get`. The adapter distinguishes:

- explicit `failed` -> provider execution failure;
- explicit `cancelled` -> provider cancellation evidence;
- JSON-RPC inability to resolve a previously bound task -> retryable `UNAVAILABLE` with
  `external_task_state_unavailable=true`;
- transport outage -> normal retryable transport unavailability.

The adapter never guesses that an unavailable task completed, failed or cancelled.

## `input_required`

The reviewed SEP exposes mid-flight requests through `inputRequests` and accepts responses via
`tasks/update`. The adapter supports two safe modes:

- with a configured invocation-scoped input handler, responses are submitted to the exact bound
  task and polling continues;
- without one, the call returns a retryable canonical `CONFLICT` with
  `mcp_task_input_required=true` while retaining the durable binding for later reconciliation.

Input request/response bodies are not written into the task binding store.

## Observability

`MCPTaskObserver` is a telemetry-only seam. It receives external binding/status observations but
cannot mutate canonical lifecycle state. Final and failure paths also expose `mcp.task`
`AdapterMetadata` containing the server ID, external task ID, external status, protocol/SEP pin
and cancellation evidence.

Raw tool result payloads, input responses and secrets are intentionally excluded from this
metadata.

## Security boundaries

The implementation preserves the existing #12/#15/#43 ownership model:

- policy and approval checks occur before `MCPToolProvider.invoke`;
- task IDs do not grant authority;
- task update/cancel operations require the exact canonical invocation binding;
- provider task status cannot authorize a side effect or bypass approval;
- sensitive payloads are not persisted in bindings or normal adapter metadata;
- no SDK/provider type is introduced into canonical contracts;
- disabling MCP or using the stable SDK-only path leaves the platform baseline unchanged.

## Compatibility recommendation

Use the Tasks path only with MCP servers that implement the reviewed final SEP-2663 semantics on
the stateless 2026 protocol family. Keep it experimental until real upstream server fixtures and
the official MCP conformance/SDK tracks provide stable task coverage for the same revision.

Promotion beyond experimental additionally depends on the platform-wide #46 acceptance profile.
#46 remains open as of the #964 implementation branch, so #964 may be implemented and tested in
isolation but must not be treated as merge/closure evidence over that declared hard dependency.

## Required regression coverage

The #964 contract tests cover:

- capability negotiation and synchronous fallback;
- rejection of unnegotiated task results;
- successful polling to an ordinary tool result;
- unknown external states failing closed;
- explicit failure vs unavailable/lost task behavior;
- cancellation scoped to the exact canonical invocation;
- durable restart/reconciliation without redispatch;
- one external handle per canonical invocation attempt;
- `input_required` / `tasks/update` flow;
- full `CapabilityInvoker -> MCP task -> ToolResult` identity preservation.

The existing non-Tasks MCP tests remain required to prove that the baseline path works with the
extension disabled.
