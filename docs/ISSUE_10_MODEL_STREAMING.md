# Issue #10 — canonical model streaming completion

Issue #10 requires `ModelProvider` to support streaming where the selected backend can provide it. The model registry and deterministic router already represented streaming as a backend-neutral capability, but the provider/runtime invocation boundary previously exposed only `generate(...) -> ModelResponse`.

This completion slice adds the missing invocation seam without making provider-native stream sessions, chunks, IDs, or SDK types canonical.

## Canonical contract

`ModelProvider` now exposes two invocation paths:

- `generate(ModelRequest) -> ModelResponse`
- `stream(ModelRequest) -> AsyncIterator[ModelStreamEvent]`

`stream` has a default implementation built from `generate`, so existing model providers remain source-compatible. A provider with native incremental output overrides `stream`.

The canonical stream event surface contains only:

- `text_delta` events for incremental user-visible text;
- one terminal `completed` event containing the canonical `ModelResponse`;
- canonical request/model references, finish reason, usage and adapter metadata.

Provider-native session objects and wire-format chunks remain behind the adapter boundary.

## Cancellation semantics

Model invocation cancellation is cooperative and provider-neutral. Cancelling the in-flight async `generate` task or the async consumer of `stream` is the canonical cancellation signal. Provider-private cancellation/session handles do not enter canonical request types.

Adapters may perform best-effort backend or transport cancellation. If an adapter does not normalize a raw `asyncio.CancelledError` itself, `ModelRuntime` converts it to canonical `ErrorCode.CANCELLED` while preserving the selected provider ID, request ID and canonical model configuration ID.

Timeout remains explicit through `OperationControl.timeout_seconds`; cancellation remains an execution-control signal rather than persisted provider state.

## Provider-native model discovery

`ModelProvider` also owns the optional `list_native_models()` discovery seam required by #10. Providers that support it advertise `list_native_models` through `descriptor.supported_operations` and return provider-native identifiers only at the adapter boundary.

Those identifiers are never canonical model IDs. `ModelRegistry` continues to own stable canonical `ModelConfiguration.config_id` values and their mapping to provider-native model names.

Providers without native inventory discovery remain source-compatible and return an empty tuple by default. Onboarding uses this provider-neutral seam instead of requiring an `OpenAICompatibleModelProvider` type check.

## ModelRuntime behavior

`ModelRuntime.stream(...)` performs the same registry/router selection used by ordinary generation. It injects the selected canonical model configuration ID before provider invocation and normalizes every returned event back to that canonical ID.

Runtime metadata preserves:

- canonical model configuration ID;
- provider instance ID;
- provider-reported model reference for diagnostics only;
- correlation ID.

The terminal response is normalized through the same response path used by `generate(...)`, so streaming cannot bypass canonical model identity checks.

`ModelRuntime.stream_canonical(...)` exposes the same behavior for `CanonicalModelRequest`.

## Local OpenAI-compatible provider

The public local/self-hosted OpenAI-compatible adapter implements native `/chat/completions` streaming with `stream: true` and Server-Sent Events.

The standard-library streaming transport:

- accepts OpenAI-compatible `data:` events;
- treats `[DONE]` as stream termination;
- emits text deltas incrementally;
- accumulates usage and finish reason;
- assembles streamed tool-call deltas for the terminal canonical response;
- preserves the existing timeout, cancellation, availability and HTTP error semantics;
- requires no paid API credential for a local endpoint.

A custom OpenAI-compatible transport that implements only the pre-existing JSON request seam continues to work through the `ModelProvider.stream` fallback.

## Decorator/wrapper invariant

A platform wrapper around a `ModelProvider` must not silently remove optional capabilities advertised by the wrapped provider.

The public `ObservedModelProvider` therefore delegates native `stream(...)` events directly instead of inheriting the generate-based fallback. It records safe model-call timing, outcome and usage telemetry without buffering the stream or capturing prompt/response bodies. It also forwards `list_native_models()` so observability does not hide provider inventory discovery.

This is important because an observability decorator is part of the normal production-shaped composition and must remain behaviorally transparent at the provider boundary.

## Replacement and routing invariants

Streaming does not change registry ownership or canonical identity rules:

1. routing selects a registered `ModelConfiguration`;
2. the provider receives that canonical configuration ID;
3. the adapter resolves its provider-native model name internally;
4. replacing the provider instance can change endpoint/native model mapping without changing the canonical model configuration ID;
5. models requested with `streaming=true` still pass through the existing deterministic streaming-capability filter.

## Regression coverage

`tests/test_issue_10_model_streaming.py` covers:

- native OpenAI-compatible text chunks and terminal response;
- canonical model ID and correlation metadata on stream events;
- no-paid-credential local streaming requests;
- fallback behavior for an existing generate-only `ModelProvider`;
- provider replacement while preserving canonical model identity;
- canonical timeout mapping during native streaming.

`tests/test_issue_10_provider_contract_completion.py` additionally covers:

- native stream preservation through the public observability wrapper;
- stream usage/timing observability without falling back to `generate`;
- provider-neutral native model discovery through wrappers/onboarding;
- source compatibility for providers without discovery;
- canonical cancellation mapping for ordinary generation;
- canonical cancellation mapping while consuming a stream.

The remaining Conversation-layer integration is intentionally not made provider-private here. Issue #72 can consume `ModelRuntime.stream_canonical(...)` and translate canonical model events into its own conversation/SSE contract.
