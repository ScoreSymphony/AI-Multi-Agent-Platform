# SGLang optional inference-backend evaluation

Repository evaluation status: **complete for #860**  
Live campaign status: **in progress under #829**  
Evaluation contract issue: **#860**  
Reference-host execution issue: **#829**  
Evaluation date: **2026-09-12**

This document records the repository-side evidence contract for deciding whether SGLang should eventually be supported as an optional self-hosted inference backend. It is deliberately not an adoption document. #860 owns the reproducible evaluation framework and source-backed pre-measurement analysis; #829 owns all execution that requires real compatible GPU/reference-host hardware and the final evidence-backed classification.

Until #829 completes the measured campaign, SGLang must not be recommended by first-run discovery and must not become a mandatory dependency.

## Ownership split

### #860 owns

- exact source/revision/license provenance for SGLang and comparison backends;
- the platform integration boundary and proof that SGLang remains optional;
- machine-readable campaign/report schemas and validation logic;
- fail-closed campaign membership and backend-revision enforcement;
- explicit comparability rules for later SGLang/vLLM measurements;
- source-backed operational comparison and deployment assumptions;
- evidence/runbook definitions for contract, performance, failure and placement campaigns;
- proof that SGLang absence does not change canonical `ModelConfiguration` identities or baseline routing.

### #829 owns

- live SGLang model startup and representative model smoke/correctness tests;
- live OpenAI-compatible behavior verification;
- same-host SGLang/vLLM latency, throughput, VRAM/RAM, batching and concurrency measurements;
- Ollama/local-path measurement where sufficiently comparable, otherwise explicit non-comparability evidence;
- live model-load failure, OOM/resource exhaustion, restart/readiness recovery, cancellation and server-loss testing;
- multi-GPU, remote Worker and multi-node evidence where actual topology permits;
- empirical installation/configuration/upgrade/support burden;
- the final classification: `supported_optional`, `experimental_only`, or `reject/defer`.

The machine-readable campaign intentionally remains `in_progress` with `decision.outcome = null` after #860 closes because its live execution lifecycle continues in #829.

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
- duplicate #19 evaluation lifecycle, #829 real-host evidence ownership or #799 discovery/setup ownership.

## Pinned upstream evidence

The live campaign is frozen to exact backend revisions so later upstream releases cannot silently change the comparison basis.

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

The following claims are supported by upstream source/documentation and may be used to design #829's measured campaign. They are **not** substitutes for platform integration measurements.

| Capability | Upstream evidence | Live status |
| --- | --- | --- |
| OpenAI-compatible `chat/completions` and `completions` | official OpenAI API tutorial | #829 contract run pending |
| Streaming | documented OpenAI-compatible and benchmark paths | #829 measured TTFT/stream semantics pending |
| Structured output | JSON Schema, regex and EBNF constraints; XGrammar default | #829 canonical test pending |
| Tool calling | explicit tool parsers and `tool_choice` modes | #829 representative model/parser test pending |
| Concurrency / batching benchmark | `bench_serving` supports request rate and max concurrency | #829 comparative measurements pending |
| TTFT / ITL / TPOT / throughput | emitted by upstream `bench_serving` | #829 comparative measurements pending |
| Prometheus metrics | `--enable-metrics`, `/metrics` | #829/#16 live mapping pending |
| Multi-GPU | tensor parallel `--tp`; data parallel `--dp` | #829 Worker-profile run pending |
| Multi-node | `--nnodes`, `--node-rank`, `--dist-init-addr` documented | #829 remote Worker run pending |
| Request/crash replay | observability docs provide request and crash dump/replay | #829 recovery suitability test pending |

## Deployment assumptions

The SGLang candidate profile is **GPU-first Linux self-hosting**. No assumption is made that SGLang is appropriate for CPU-only, low-RAM VPS or every local workstation profile. vLLM is the fixed GPU-serving comparator; Ollama is the fixed lighter local-path comparator for this campaign.

Every #829 run must record the environment and workload fields declared by `config/inference-backend-evaluation.sglang-v0.5.19.json`, including the immutable platform/backend/model revisions, quantization/dtype, launch configuration, Worker and hardware identity, accelerator/runtime/driver, topology, cache state and workload parameters.

Raw benchmark output must be retained. Summary tables without raw evidence are insufficient. All v1 metric keys remain present in each report; a genuinely unmeasured metric is recorded as `null`, never as a fabricated numeric zero. A decision-eligible SGLang-vLLM pair must contain non-null values for every metric listed in the campaign's `required_metrics`.

## Representative comparison matrix

SGLang and vLLM performance rows must use the same model revision and equivalent quantization/dtype. Ollama must use the same representative model representation where sufficiently equivalent. If equivalence is impossible, the local-path run must be marked non-comparable instead of normalizing away the difference.

| Dimension | SGLang | vLLM | Ollama local path |
| --- | --- | --- | --- |
| OpenAI contract | #829 pending | #829 pending | #829 pending where supported |
| cold model load | #829 pending | #829 pending | #829 pending |
| warm restart | #829 pending | #829 pending | #829 pending |
| streaming TTFT | #829 pending | #829 pending | #829 pending where comparable |
| steady-state TPOT / ITL | #829 pending | #829 pending | #829 pending where comparable |
| request throughput | #829 pending | #829 pending | #829 pending where comparable |
| concurrency 1 / 4 / 16 / 32 | #829 pending | #829 pending | #829 pending where comparable |
| VRAM peak / steady | #829 pending | #829 pending | #829 pending |
| host RAM peak / steady | #829 pending | #829 pending | #829 pending |
| structured JSON | #829 pending | #829 pending | #829 pending where supported |
| tool calling | #829 pending | #829 pending | #829 pending where supported |
| cancellation | #829 pending | #829 pending | #829 pending |
| backend unavailable | #829 pending | #829 pending | #829 pending |
| OOM behavior / recovery | #829 pending | #829 pending | #829 pending |
| multi-GPU | #829 pending | #829 pending | not required for local-path role |
| multi-node / remote Worker | #829 pending | #829 pending | not required for local-path role |
| operational upgrade burden | #829 pending | #829 pending | #829 pending |

The source-backed pre-measurement operational comparison is recorded in `docs/evaluations/SGLANG_BACKEND_OPERATIONAL_COMPARISON.md`. It satisfies #860's documentation-side comparison; it does not substitute for #829's measured installation, resource, latency or recovery evidence.

## Repository-side campaign contract (#860)

The machine-readable campaign definition lives in `config/inference-backend-evaluation.sglang-v0.5.19.json` and defines:

- canonical contract cases, including non-streaming/streaming, cancellation, structured JSON, tools, error normalization, model-ID stability and candidate-absent baseline routing;
- performance scenarios at concurrency 1, 4, 16 and 32;
- required latency, throughput, VRAM/RAM and error metrics;
- failure/recovery cases;
- placement cases;
- exact environment metadata;
- raw-evidence retention requirements.

The versioned report schema lives at `src/ai_multi_agent_platform/benchmarking/schemas/inference-backend-evaluation-report.v1.schema.json`.

`ai_multi_agent_platform.benchmarking.inference_backend_evaluation` and `platform-inference-backend-evaluation` provide the fail-closed evidence gate. The gate rejects:

- reports for a different campaign;
- undeclared backends;
- revision drift from the pinned SGLang/vLLM/Ollama commits;
- incomparable performance evidence;
- decision-eligible pairs with missing required metrics;
- missing/latest-failing mandatory SGLang contract or failure coverage.

Comparability is independently rechecked across platform commit, model/revision, dtype/quantization, Worker set, OS/runtime/driver, GPU/CPU/RAM, topology, cache state and workload dimensions rather than trusting a report flag alone.

## Real-host campaign (#829)

#829 executes the runbook in `docs/evaluations/SGLANG_EVIDENCE_RUNBOOK.md`. It must retain real evidence for:

1. representative live model readiness and platform contract behavior;
2. same-host SGLang/vLLM performance under the exact comparison contract;
3. Ollama/local-path evidence or explicit non-comparability;
4. OOM, cancellation, process/server loss, restart/readiness and resource recovery;
5. single-GPU plus any actually supported multi-GPU/remote/multi-node placements;
6. actual installation/configuration/upgrade/support burden.

Unavailable topology is recorded as `not_measured`, not converted into a pass.

## Evidence status on 2026-09-12

| Evidence | Owner | Status |
| --- | --- | --- |
| SGLang source/release/license pin | #860 | complete |
| vLLM comparator pin | #860 | complete |
| Ollama local comparator pin | #860 | complete |
| source-backed capability research | #860 | complete |
| source-backed operational comparison | #860 | complete |
| architecture/provider boundary | #860 | complete |
| reproducible campaign/report contracts | #860 | complete |
| revision/comparability/readiness validator + CLI | #860 | complete |
| candidate-absence routing regression guard | #860 | complete |
| live SGLang model smoke | #829 | pending real host |
| SGLang vs vLLM performance | #829 | pending real host |
| Ollama local-path measurement/non-comparability | #829 | pending real host |
| VRAM/RAM and latency/throughput evidence | #829 | pending real host |
| failure/recovery campaign | #829 | pending real host |
| multi-GPU/remote Worker evidence | #829 | pending compatible topology |
| final SGLang classification | #829 | pending live evidence |
| #799 first-run recommendation | #799 | waits for #829 classification |

## Final classification gate (#829)

The only valid final outcomes remain:

- `supported_optional` — contract coverage passes and the measured operational/performance profile justifies a maintained optional backend;
- `experimental_only` — useful and functional, but compatibility, recovery, operational burden or hardware coverage is not strong enough for a normal recommendation;
- `reject/defer` — evidence does not justify integration/maintenance now.

A final outcome requires all mandatory SGLang contract and failure cases plus at least one comparable SGLang-vLLM performance run on the same Worker/model revision with all campaign-required metrics actually measured. Missing multi-node hardware may remain explicitly `not_measured` if the decision does not claim multi-node support. The lighter local-path evidence must be considered even when exact model representation makes a direct performance comparison non-comparable.

Until #829 records that classification, #799 must treat SGLang as **evaluated candidate, not recommended backend**.

## #860 completion statement

#860 is complete when this repository-side contract, provenance, documentation, validator/CLI and regression coverage are merged. Real GPU/reference-host execution and the final backend policy outcome are intentionally outside #860's Definition of Done and are tracked by #829.
