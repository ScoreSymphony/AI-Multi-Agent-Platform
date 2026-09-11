# SGLang optional inference-backend evaluation

Status: **in progress**  
Issue: **#860**  
Evaluation date: **2026-09-12**

This document records evidence for deciding whether SGLang should be supported as an optional self-hosted inference backend. It is deliberately not an adoption document. Until the measured campaign below is complete, SGLang must not be recommended by first-run discovery and must not become a mandatory dependency.

## Architecture boundary

The platform contract remains:

```text
ModelRequest
  -> ModelRuntime
  -> ModelRegistry
  -> ModelProvider
  -> optional provider/backend implementation
```

SGLang is allowed only behind the existing platform-owned `ModelProvider` boundary from #10. `ModelConfiguration.config_id` remains the canonical model identity. SGLang-native model names, launch flags, endpoint URLs, worker addresses and runtime revisions are adapter metadata or deployment configuration; they are never canonical model IDs.

This evaluation must not:

- make SGLang the canonical model API;
- add SGLang to required package dependencies;
- bypass `ModelRegistry` / `ModelRouter`;
- teach Task, Agent or orchestration code about SGLang-native objects;
- make the reference/non-SGLang path depend on SGLang availability;
- duplicate the #19 evaluation lifecycle or the #799 discovery/setup ownership.

## Pinned upstream evidence

The measured campaign is frozen to exact backend revisions so later upstream releases cannot silently change the comparison basis.

| Backend | Role | Release | Commit | License |
| --- | --- | --- | --- | --- |
| SGLang | candidate | `v0.5.19` | `0bcd822377da7b5718e674eaf9c870d349424dd1` | Apache-2.0 |
| vLLM | GPU-serving comparator | `v0.29.0` | `98dff2a81d747d1dba01a47f939f48c3526d4206` | Apache-2.0 |
| Ollama | lighter local-path comparator | `v0.34.0` | `d8ab4b4f0ca24b51d3a46b3bf4f462e58ce66b1f` | MIT |

Machine-readable source/revision/license provenance is stored in:

- `config/inference-backend-upstream.sglang-v0.5.19.json`;
- `config/inference-backend-upstream.vllm-v0.29.0.json`;
- `config/inference-backend-upstream.ollama-v0.34.0.json`.

Primary SGLang upstream sources used to design the campaign:

- repository / license: <https://github.com/sgl-project/sglang>
- release `v0.5.19`: <https://github.com/sgl-project/sglang/releases/tag/v0.5.19>
- OpenAI-compatible completions: <https://github.com/sgl-project/sglang/blob/main/docs_new/docs/basic_usage/openai_api_completions.mdx>
- structured outputs: <https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/structured_outputs.mdx>
- tool parser: <https://docs.sglang.io/docs/advanced_features/tool_parser>
- serving benchmark: <https://docs.sglang.ai/developer_guide/bench_serving>
- observability: <https://docs.sglang.ai/advanced_features/observability.html>
- server arguments: <https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/server_arguments.md>

The exact release commits are the reproducibility anchors. Moving `main`/`stable` documentation is capability-discovery evidence only and is not evidence that every documented feature behaves identically at a pinned revision.

## Verified upstream capabilities

The following claims are supported by upstream source/documentation and may be used to design the measured campaign. They are **not** substitutes for platform integration measurements.

| Capability | Upstream evidence | Platform status |
| --- | --- | --- |
| OpenAI-compatible `chat/completions` and `completions` | official OpenAI API tutorial | contract test pending |
| Streaming | documented OpenAI-compatible and benchmark paths | measured TTFT/stream semantics pending |
| Structured output | JSON Schema, regex and EBNF constraints; XGrammar default | canonical structured-output test pending |
| Tool calling | explicit tool parsers and `tool_choice` modes | representative model/parser test pending |
| Concurrency / batching benchmark | `bench_serving` supports request rate and max concurrency | comparative measurements pending |
| TTFT / ITL / TPOT / throughput | emitted by upstream `bench_serving` | comparative measurements pending |
| Prometheus metrics | `--enable-metrics`, `/metrics` | #16 integration mapping pending |
| Multi-GPU | tensor parallel `--tp`; data parallel `--dp` | Worker-profile run pending |
| Multi-node | `--nnodes`, `--node-rank`, `--dist-init-addr` documented | remote Worker run pending |
| Request/crash replay | observability docs provide request and crash dump/replay | recovery suitability test pending |

## Deployment assumptions to verify

The SGLang candidate profile is **GPU-first Linux self-hosting**. No assumption is made that SGLang is appropriate for CPU-only, low-RAM VPS or every local workstation profile. vLLM is the fixed GPU-serving comparator; Ollama is the fixed lighter local-path comparator for this campaign.

The measured campaign must record, per run:

- platform commit;
- exact backend revision and image/package identity;
- exact model and model revision;
- quantization / dtype;
- launch command and relevant backend flags;
- OS/kernel, Python, CUDA/ROCm and driver versions;
- GPU model/count/VRAM and CPU/RAM;
- Worker/node identity and network topology for distributed runs;
- warmup policy, request count, input/output lengths, request rate and concurrency;
- endpoint authentication / exposure assumptions;
- start, stop, restart and cache state.

Raw benchmark output must be retained. Summary tables without the raw evidence are insufficient for a final #860 decision.

## Representative comparison matrix

SGLang and vLLM performance rows must use the same model revision and equivalent quantization/dtype. Ollama must use the same representative model representation where sufficiently equivalent. If equivalence is impossible, the local-path run must be marked non-comparable instead of normalizing away the difference.

| Dimension | SGLang | vLLM | Ollama local path |
| --- | --- | --- | --- |
| OpenAI contract | pending | pending | pending where supported |
| cold model load | pending | pending | pending |
| warm restart | pending | pending | pending |
| streaming TTFT | pending | pending | pending where comparable |
| steady-state TPOT / ITL | pending | pending | pending where comparable |
| request throughput | pending | pending | pending where comparable |
| concurrency 1 / 4 / 16 / 32 | pending | pending | pending where comparable |
| VRAM peak / steady | pending | pending | pending |
| host RAM peak / steady | pending | pending | pending |
| structured JSON | pending | pending | pending where supported |
| tool calling | pending | pending | pending where supported |
| cancellation | pending | pending | pending |
| backend unavailable | pending | pending | pending |
| OOM behavior / recovery | pending | pending | pending |
| multi-GPU | pending | pending | not required for local-path role |
| multi-node / remote Worker | pending | pending | not required for local-path role |
| operational upgrade burden | pending | pending | pending |

The source-backed pre-measurement operational comparison is recorded in `docs/evaluations/SGLANG_VLLM_OPERATIONAL_COMPARISON.md`. It narrows the live test plan but does not substitute for measured installation, resource, latency or recovery evidence.

## Required campaign

The machine-readable campaign definition lives in
`config/inference-backend-evaluation.sglang-v0.5.19.json`.

### 1. Contract coverage

Run the same canonical model requests through the platform `ModelRuntime -> ModelProvider` path for each backend where the backend claims the relevant operation. Required SGLang cases:

1. model discovery/health;
2. non-streaming chat completion;
3. streaming chat completion and cancellation;
4. structured JSON output validated locally against the requested schema;
5. tool declaration + tool call round trip for a model/parser combination explicitly supported by the backend;
6. timeout, unavailable endpoint and malformed response normalization;
7. backend removal/unavailability without changing canonical `ModelConfiguration.config_id`;
8. SGLang absent from the host: reference routing and non-SGLang providers behave unchanged.

The OpenAI-compatible wire surface is an adapter detail. Passing raw `/v1/chat/completions` requests is necessary interoperability evidence but is not sufficient platform contract evidence.

### 2. Performance measurements

Use an identical request corpus and fixed model revision for the decision-eligible SGLang-vLLM comparison. At minimum retain:

- cold load time;
- ready-to-first-request time;
- request throughput (req/s);
- input/output/total token throughput when token counts are available;
- end-to-end p50/p95/p99;
- TTFT p50/p95/p99;
- ITL or TPOT p50/p95/p99;
- failure/error count;
- peak and steady VRAM;
- peak and steady host RAM;
- CPU utilization where available.

SGLang's `python -m sglang.bench_serving` may be used as one raw measurement source because it exposes TTFT, ITL, TPOT, throughput, concurrency control and JSONL output. The final platform evidence should also retain platform-side telemetry from #16 so a backend's own benchmark is not the sole source of truth.

### 3. Failure and recovery

Exercise explicitly:

- endpoint unavailable before dispatch;
- server termination during an in-flight streaming request;
- cancellation from the canonical operation control path;
- load failure for an invalid/incompatible model;
- intentional OOM or resource exhaustion on a bounded test Worker where safe;
- process restart and readiness recovery;
- Worker loss during a remote/multi-node run;
- repeated start/stop to detect leaked GPU memory or stale readiness.

Recovery must be observed through platform-owned health/routing state. A provider-native health endpoint is evidence input, not canonical lifecycle state.

### 4. Multi-GPU and remote Worker

At least one compatible model must run on:

- one GPU;
- multiple GPUs on one Worker when hardware permits;
- a remote GPU Worker controlled by the platform;
- multiple nodes only if the available hardware/network makes the upstream topology valid.

A missing suitable multi-node environment is reported as `not_measured`, not as a pass.

## Evidence status on 2026-09-12

| Evidence | Status | Reason |
| --- | --- | --- |
| SGLang source/release/license pinned | complete | `v0.5.19`, exact commit, Apache-2.0 |
| vLLM comparator pinned | complete | `v0.29.0`, exact commit, Apache-2.0 |
| Ollama local comparator pinned | complete | `v0.34.0`, exact commit, MIT |
| documented API/capability research | complete | official upstream sources captured separately from measured claims |
| source-backed operational surface | complete | comparison document records install/distribution/topology facts without performance claims |
| architecture boundary | complete | remains behind #10 `ModelProvider`; #19/#799 ownership preserved |
| reproducible campaign/report contracts | complete | machine-readable campaign, report schema, revision validation and readiness CLI checked in |
| live SGLang model smoke | not measured | requires compatible GPU Worker/runtime |
| SGLang vs vLLM performance | not measured | requires same model/hardware campaign |
| Ollama local-path measurement | not measured | requires same workload and explicit comparability record |
| VRAM/RAM evidence | not measured | requires live Worker telemetry |
| failure/recovery campaign | not measured | requires live backend process/Worker |
| multi-GPU/remote Worker | not measured | requires compatible hardware/topology |
| #799 first-run recommendation | blocked | #799 is still under implementation; recommendation must consume completed #860 evidence |

## Decision gate

No final #860 classification is asserted yet. The only valid final outcomes are:

- `supported_optional` — contract coverage passes and the measured operational/performance profile justifies a maintained optional backend;
- `experimental_only` — useful and functional, but compatibility, recovery, operational burden or hardware coverage is not strong enough for a normal recommendation;
- `reject/defer` — evidence does not justify integration/maintenance now.

A final outcome requires all mandatory SGLang contract and failure cases plus at least one comparable SGLang-vLLM performance run on the same Worker/model revision. Submitted reports for SGLang, vLLM or Ollama must use the exact campaign-pinned backend revision. Missing multi-node hardware may remain explicitly `not_measured` if the decision does not claim multi-node support. The lighter local-path evidence must be considered in the final policy classification even when exact model representation makes a direct performance comparison non-comparable.

Until that gate is met, #799 must treat SGLang as **evaluated candidate, not recommended backend**.
