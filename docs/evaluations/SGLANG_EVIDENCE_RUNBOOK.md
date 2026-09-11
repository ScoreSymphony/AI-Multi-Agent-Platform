# SGLang evidence runbook for issue #860

This runbook turns the #860 evaluation plan into a reproducible Worker-side evidence procedure. It does not classify SGLang by itself and it must not be used to synthesize measurements that were not observed on real hardware.

## Pinned upstream and comparators

The campaign uses three exact backend revisions:

- SGLang candidate: `v0.5.19`, commit `0bcd822377da7b5718e674eaf9c870d349424dd1`, Apache-2.0;
- vLLM GPU-serving comparator: `v0.29.0`, commit `98dff2a81d747d1dba01a47f939f48c3526d4206`, Apache-2.0;
- Ollama lighter local-path comparator: `v0.34.0`, commit `d8ab4b4f0ca24b51d3a46b3bf4f462e58ce66b1f`, MIT.

For SGLang, the exact annotated tag object verified on 2026-09-12 is `59f20bffdde59a35cc628372d85d20a979f6271b`; the release publication timestamp is `2026-09-05T02:27:42Z` and the release-tag license blob is `9c422689c8f5c317c7c65153b1209349ec57007e`.

Machine-readable upstream provenance is stored in:

- `config/inference-backend-upstream.sglang-v0.5.19.json`;
- `config/inference-backend-upstream.vllm-v0.29.0.json`;
- `config/inference-backend-upstream.ollama-v0.34.0.json`.

A report for another pinned-backend commit is not evidence for this campaign even if it reuses the same `campaign_id`.

## Evidence ownership

All platform-facing evidence remains owned by the existing platform contracts:

- #10 owns canonical model/provider identity and routing;
- #14 owns Worker/Node identity and placement;
- #16 owns platform telemetry;
- #19 owns evaluation/regression semantics;
- #34 owns secrets/configuration;
- #799 may consume the final classification for first-run recommendations.

Backend-specific endpoint names, model names, launch flags and process metadata are adapter/deployment evidence only. They never become canonical `ModelConfiguration.config_id` values.

## Required artifacts per measured run

Every measured backend run must produce one JSON report conforming to
`src/ai_multi_agent_platform/benchmarking/schemas/inference-backend-evaluation-report.v1.schema.json`
and retain the raw evidence referenced by that report.

At minimum retain:

1. exact platform commit;
2. backend package/image identity and backend revision;
3. model ID, immutable model revision and dtype/quantization;
4. Worker IDs plus GPU/CPU/RAM/runtime/driver metadata;
5. exact launch command;
6. a stable `scenario_id` identifying the fixed request corpus and workload shape;
7. request count, warmup, input/output-length policy, request rate and concurrency;
8. cold-load and ready-to-first-request timing;
9. request/token throughput, end-to-end latency, TTFT, ITL/TPOT, VRAM and host-RAM metrics;
10. contract-case and failure-case results with evidence references;
11. placement result;
12. raw benchmark/log/telemetry file paths and SHA-256 hashes.

Every metric key remains present in the v1 report. If a metric was genuinely not measured, record `null`; never use numeric zero as a substitute for unknown evidence. A report may still be retained for contract/failure evidence with `null` metrics, but a SGLang-vLLM performance pair is decision-eligible only when all metrics listed in the campaign's `required_metrics` are non-null on both reports.

`backend_revision` must equal the full commit pinned for SGLang, vLLM or Ollama in the campaign. The package/image field may additionally record a release tag, image digest or package version.

`scenario_id` must uniquely identify the request corpus used for the comparison. Two reports with different scenario IDs are not treated as performance-comparable even if their other dimensions match.

## Comparison discipline

A decision-eligible SGLang-vLLM pair must use the same:

- platform commit;
- canonical model target and immutable model revision;
- dtype/quantization;
- Worker set;
- OS/kernel, accelerator runtime and driver;
- GPU model/count/VRAM profile;
- CPU/host-RAM profile and network topology;
- cache state;
- scenario/request corpus;
- request count and warmup policy;
- input/output-length policy;
- request rate;
- concurrency.

If any of these differ, set `comparability.comparable` to `false` and explain the difference in `comparability.reasons`. Do not normalize non-equivalent runs into a headline performance claim. The platform-side readiness gate independently rechecks these dimensions rather than trusting the flag alone.

Ollama is the fixed lighter local-path comparator for this campaign. A direct SGLang-vLLM performance claim does not require Ollama to share an identical GPU-serving topology, but an Ollama run must still retain its exact model/revision/quantization and environment. If the same representative model representation cannot be made sufficiently equivalent, record the Ollama run as non-comparable and state why rather than manufacturing normalized numbers.

## Worker-side execution sequence

For each backend and scenario:

1. record the clean starting state and platform commit;
2. record hardware/runtime metadata before launch;
3. start the backend with the exact retained command/configuration;
4. measure cold-load and readiness time;
5. run canonical platform contract smoke coverage through `ModelRuntime -> ModelProvider`;
6. run the fixed benchmark corpus after the declared warmup;
7. collect backend-native benchmark output and #16 platform telemetry;
8. capture VRAM/RAM before, during and after the run;
9. exercise the relevant failure/recovery case when the run is a failure campaign;
10. stop/restart the backend when required and verify readiness/resource recovery;
11. hash retained raw files with SHA-256;
12. write and schema-validate the report;
13. mark `decision_eligible=true` only when the report is a real measured run suitable for a final comparison.

## Failure campaign

The SGLang campaign must cover the IDs from
`config/inference-backend-evaluation.sglang-v0.5.19.json`, including:

- endpoint unavailable before dispatch;
- server termination during streaming;
- canonical cancellation;
- model-load failure;
- bounded OOM/resource exhaustion;
- process restart/readiness recovery;
- remote Worker loss;
- repeated start/stop resource recovery.

A provider-native health endpoint is not sufficient recovery evidence. Recovery must become visible through platform-owned health/routing state.

## Placement campaign

Record explicit evidence for:

- single-GPU Worker;
- multi-GPU single Worker where compatible hardware exists;
- remote GPU Worker;
- multi-node only when the available topology is valid for the upstream deployment mode.

Unavailable hardware is `not_measured`, not `pass`.

## Decision-readiness gate

`ai_multi_agent_platform.benchmarking.inference_backend_evaluation` validates measured reports and computes decision readiness. The installed CLI entry point is `platform-inference-backend-evaluation`.

Example after measured reports have been collected:

```bash
platform-inference-backend-evaluation \
  --campaign config/inference-backend-evaluation.sglang-v0.5.19.json \
  --report evidence/sglang-single-gpu.json \
  --report evidence/vllm-single-gpu.json \
  --report evidence/ollama-local-path.json \
  --output evidence/issue-860-readiness.json \
  --require-ready
```

Exit codes are:

- `0`: reports validate; with `--require-ready`, the decision gate is ready;
- `2`: malformed campaign/report input or schema/IO validation failure;
- `3`: reports validate, but `--require-ready` found insufficient decision evidence.

The gate requires:

- every submitted report to name a backend explicitly declared by this campaign;
- every submitted report for pinned SGLang/vLLM/Ollama to equal that backend's full campaign commit;
- all mandatory SGLang contract cases to have a latest passing result;
- all mandatory SGLang failure/recovery cases to have a latest passing result;
- all campaign-required performance metrics to be actually measured rather than `null` on the decision-eligible SGLang-vLLM pair;
- at least one decision-eligible SGLang-vLLM performance pair whose comparison dimensions actually match.

Latest-result selection uses absolute ISO-8601 timestamps rather than string ordering, so evidence from Workers with different timezone offsets is ordered correctly.

Placement coverage is reported separately so missing hardware remains visible rather than being silently converted into success.

Passing the readiness gate means the repository has enough core evidence to make the SGLang-vLLM policy choice. It does **not** automatically choose among `supported_optional`, `experimental_only`, and `reject/defer`; that final classification must also reflect the measured resource/performance/operational results, the lighter local-path evidence and the support burden.

## Current blocker

As of 2026-09-12 the repository-side preparation is reproducible, but #860 still lacks live compatible-GPU Worker measurements. Those measurements must not be fabricated from upstream benchmark claims. In addition, #799 remains open and therefore cannot consume a final recommendation yet.
