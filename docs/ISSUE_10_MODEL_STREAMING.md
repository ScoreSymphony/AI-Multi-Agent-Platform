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

The public local/self-hosted OpenAI-compatible adapter now implements native `/chat/completions` streaming with `stream: true` and Server-Sent Events.

The standard-library streaming transport:

- accepts OpenAI-compatible `data:` events;
- treats `[DONE]` as stream termination;
- emits text deltas incrementally;
- accumulates usage and finish reason;
- assembles streamed tool-call deltas for the terminal canonical response;
- preserves the existing timeout, cancellation, availability and HTTP error semantics;
- requires no paid API credential for a local endpoint.

A custom OpenAI-compatible transport that implements only the pre-existing JSON request seam continues to work through the `ModelProvider.stream` fallback.

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

The remaining Conversation-layer integration is intentionally not made provider-private here. Issue #72 can consume `ModelRuntime.stream_canonical(...)` and translate canonical model events into its own conversation/SSE contract.
