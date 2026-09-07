# Model, tool and provider degradation benchmarks

Issue: #440

This benchmark profile measures deterministic provider degradation through the platform's existing provider-neutral runtime seams. It is designed to separate platform overhead from provider service time without introducing paid services, provider-native control paths or benchmark-only production bypasses.

## Canonical paths exercised

Model scenarios use the normal platform path:

```text
ModelRequest
  -> DeterministicModelRouter
  -> ModelRegistry
  -> ModelRuntime
  -> ModelProvider.generate()
```

Tool scenarios use the normal capability path:

```text
CapabilityInvocation
  -> CapabilityRegistry.resolve()
  -> CapabilityInvoker
  -> ToolProvider.invoke()
```

The deterministic benchmark providers implement the same `ModelProvider` and `CapabilityToolProvider` contracts used by real adapters. They exist only as local fixtures behind those stable boundaries.

The benchmark deliberately relies on existing runtime semantics rather than duplicating them:

- model cancellation is normalized by `ModelRuntime` to canonical non-retryable `cancelled`;
- tool timeout is enforced by `CapabilityInvoker` and normalized to canonical retryable `timeout`;
- tool cancellation is normalized by `CapabilityInvoker` to canonical retryable `cancelled`;
- provider unavailability crosses the provider boundary as canonical, retryable `unavailable`;
- no automatic retry loop is added by the benchmark harness.

## Scenarios

`model-latency`
: Adds deterministic provider service delay while requests continue to succeed. This is the primary profile for distinguishing provider time from platform overhead.

`model-unavailable`
: The selected deterministic model provider returns canonical retryable `unavailable` during the fault phase, then returns to healthy service.

`model-cancelled`
: Concurrent model requests are cancelled while the provider is in-flight. `ModelRuntime` must expose canonical non-retryable `cancelled`, and provider coroutine cancellation must be observable.

`tool-unavailable`
: The resolved deterministic tool provider returns canonical retryable `unavailable`, followed by a healthy recovery phase.

`tool-timeout`
: The provider deliberately exceeds the capability timeout. The real `CapabilityInvoker` timeout path must cancel the provider call and expose canonical retryable `timeout`.

`tool-cancelled`
: Concurrent canonical capability invocations are cancelled while the provider is in-flight. `CapabilityInvoker` must expose canonical retryable `cancelled` and the provider coroutine must receive cancellation.

## Three-phase evidence

Every run contains equal-sized `baseline`, `fault` and `recovery` phases. The same bounded concurrency is used in all phases so the result can answer two separate questions:

1. What changed while the provider was degraded or unavailable?
2. Did successful canonical execution return after the fault was removed?

A result is invalid when an unexpected success or unexpected failure occurs, the canonical error category/retryability differs from the scenario contract, or recovery does not restore successful invocations.

## Latency separation

For every operation the fixture records its own provider service duration. The harness separately records end-to-end duration through the canonical platform path and derives:

```text
platform overhead = end-to-end latency - provider service latency
```

The report publishes p50/p95/p99 distributions for all three values by phase:

- `phase_latency` — complete canonical invocation duration;
- `provider_service_latency` — time spent inside the deterministic provider fixture;
- `platform_overhead_latency` — non-negative derived remainder.

This is intended for comparative platform evidence. It does not claim nanosecond-accurate attribution and should not be used to infer hardware-independent absolute limits.

## Retry semantics

Canonical failures retain their actual `retryable` classification in the report through `retryable_error_counts`. The model and tool runtime paths are intentionally not flattened into one synthetic policy: current `ModelRuntime` cancellation is non-retryable, while `CapabilityInvoker` timeout and cancellation are retryable. Provider unavailability is retryable on both exercised paths.

The benchmark does **not** invent a retry policy where the exercised runtime does not currently own one; `automatic_retry_attempts` is therefore expected to remain `0` for this profile.

A future retry-owning runtime can add an explicit benchmark profile when its semantics are stable. Until then, this profile proves failure classification, timeout/cancellation behavior and recovery without creating an uncontrolled retry/spin loop.

## Running locally

After installing the project, run for example:

```bash
platform-provider-fault \
  --scenario model-unavailable \
  --operations-per-phase 100 \
  --concurrency 10 \
  --fault-delay-seconds 0.05 \
  --operation-timeout-seconds 2 \
  --output artifacts/benchmarks/provider-model-unavailable.json
```

For the latency-separation profile:

```bash
platform-provider-fault \
  --scenario model-latency \
  --operations-per-phase 100 \
  --concurrency 10 \
  --fault-delay-seconds 0.05 \
  --output artifacts/benchmarks/provider-model-latency.json
```

For the canonical tool-timeout path, the provider delay must be greater than the configured tool timeout:

```bash
platform-provider-fault \
  --scenario tool-timeout \
  --operations-per-phase 100 \
  --concurrency 10 \
  --fault-delay-seconds 0.05 \
  --tool-timeout-seconds 0.01 \
  --output artifacts/benchmarks/provider-tool-timeout.json
```

## Safety bounds

The harness refuses configurations that exceed its explicit operation or concurrency safety caps. Every operation also has an outer bounded operation timeout so a broken fixture cannot leave the benchmark hanging indefinitely.

Defaults are deliberately small enough for development use. Larger integration/release measurements should increase workload sizes only within the documented host budget and retain the environment metadata emitted with each report.

## Result artifact

Reports conform to:

`docs/schemas/benchmark-provider-fault.v1.schema.json`

They contain:

- benchmark/version/scenario/configuration;
- platform commit and environment metadata;
- per-phase throughput;
- end-to-end, provider-service and derived platform-overhead latency distributions;
- canonical error and retryable-error counts;
- provider coroutine cancellation evidence;
- process CPU, traced memory, peak RSS and open-file metrics where available;
- correctness and recovery results.

No provider credentials, prompts from real users, secret values or provider-native endpoint configuration are needed or persisted.

## CI profile

`.github/workflows/provider-fault-smoke.yml` runs all six scenarios with tiny bounded workloads on pull requests and validates both behavioral invariants and the versioned report schema. This smoke proves harness/runtime semantics, not a production operating envelope.

## Scope boundary

This block closes the deterministic model/tool/provider-unavailability, latency-separation, timeout and cancellation evidence requested by #440.

It does not yet provide:

- real external-provider network/SaaS latency claims;
- an automatic retry benchmark where no canonical retry-owning runtime exists;
- persistence lock/contention or injected transaction failures;
- distributed Worker loss/rejoin or Workspace materialization faults;
- HA promotion/failover evidence.

Those remain separate progressive #440 profiles and should use their own supported seams rather than being simulated inside this provider benchmark.
