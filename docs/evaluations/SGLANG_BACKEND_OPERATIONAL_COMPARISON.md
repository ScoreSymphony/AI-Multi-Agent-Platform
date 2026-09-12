# SGLang, vLLM and Ollama operational comparison

Status: **source-backed pre-measurement comparison complete for #860**  
Repository evaluation issue: **#860**  
Live reference-host evidence issue: **#829**  
Observed: **2026-09-12**

This document records the operational facts that can be established before the live GPU campaign. It is deliberately separate from measured performance and recovery evidence. No throughput, latency, memory, startup, reload or reliability conclusion may be inferred from this document. All such empirical evidence is owned by #829.

## Pinned comparison revisions

| Backend | Role | Release | Commit | License |
| --- | --- | --- | --- | --- |
| SGLang | candidate | `v0.5.19` | `0bcd822377da7b5718e674eaf9c870d349424dd1` | Apache-2.0 |
| vLLM | GPU-serving comparator | `v0.29.0` | `98dff2a81d747d1dba01a47f939f48c3526d4206` | Apache-2.0 |
| Ollama | lighter local-path comparator | `v0.34.0` | `d8ab4b4f0ca24b51d3a46b3bf4f462e58ce66b1f` | MIT |

Machine-readable provenance is stored in:

- `config/inference-backend-upstream.sglang-v0.5.19.json`;
- `config/inference-backend-upstream.vllm-v0.29.0.json`;
- `config/inference-backend-upstream.ollama-v0.34.0.json`.

Measured reports using another pinned backend revision are not evidence for this campaign. A newer release requires a new or explicitly revised campaign rather than silently replacing a comparator. llama.cpp remains a valid future/local-path candidate, but it is not mixed with Ollama under one ambiguous report identity in this campaign.

## Source-backed operational surface

| Dimension | SGLang | vLLM | Ollama | Repository-side conclusion |
| --- | --- | --- | --- | --- |
| License | Apache-2.0 at pinned tag | Apache-2.0 at pinned tag | MIT at pinned tag | no license blocker identified for evaluation |
| Installation surface | pip/uv, source and Docker paths documented | packaged installation paths and platform-specific wheels documented | install scripts plus Linux/macOS platform archives published | all three have self-hosted distribution paths; actual maintenance effort belongs to #829 |
| Target profile | evaluated primarily as GPU-serving backend on Linux Workers | fixed GPU-serving comparator | fixed lighter local-path comparator | GPU-serving results must not be generalized to every CPU/VPS profile |
| Hardware breadth in upstream evidence | current docs describe NVIDIA as common path plus separate AMD/CPU/TPU/XPU/other guides | current docs list NVIDIA CUDA, AMD ROCm, Intel XPU, Apple Silicon and CPU platforms | pinned release assets include Linux AMD64/ARM64, macOS and ROCm/JetPack variants | discovery evidence only, not model-level compatibility proof |
| Single-node multi-GPU | tensor/data parallel controls documented | tensor/pipeline/data/expert parallel controls documented | not required for local-path role | real Worker behavior belongs to #829 |
| Multi-node serving | `--nnodes`, `--node-rank`, `--dist-init-addr` documented | multi-node serving and node/rank deployment paths documented | outside lighter local-path purpose | topology exists upstream; platform-owned behavior remains empirical under #829 |
| OpenAI-compatible serving | documented | documented | used where pinned surface supports the requested operation | wire compatibility alone is insufficient; live `ModelProvider` behavior belongs to #829 |
| Release packaging | `v0.5.19` has no attached GitHub release assets; docs point to package/Docker channels | `v0.29.0` publishes platform-specific wheel assets | `v0.34.0` publishes installers and packaged archives | distribution mechanics differ; actual install/upgrade burden belongs to #829 |
| Failure/recovery | native health/observability features are evidence inputs only | native server/distributed controls are evidence inputs only | local server lifecycle is evidence input only | no reliability advantage is claimed without #829 live evidence |

## Evidence sources

Pinned revision/license evidence:

- SGLang release: <https://github.com/sgl-project/sglang/releases/tag/v0.5.19>
- SGLang tag/license provenance: `config/inference-backend-upstream.sglang-v0.5.19.json`
- vLLM release: <https://github.com/vllm-project/vllm/releases/tag/v0.29.0>
- vLLM tag/license provenance: `config/inference-backend-upstream.vllm-v0.29.0.json`
- Ollama release: <https://github.com/ollama/ollama/releases/tag/v0.34.0>
- Ollama tag/license provenance: `config/inference-backend-upstream.ollama-v0.34.0.json`

Operational capability discovery observed on 2026-09-12:

- SGLang installation: <https://docs.sglang.io/docs/get-started/install>
- SGLang multi-node/server arguments: <https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/server_arguments.md>
- vLLM installation: <https://docs.vllm.ai/en/stable/getting_started/installation/>
- vLLM serving CLI: <https://docs.vllm.ai/en/stable/cli/serve/>
- vLLM multi-node example: <https://docs.vllm.ai/en/stable/examples/ray_serving/multi-node-serving/>

Moving `main`/`stable` documentation is capability-discovery evidence only. Exact release commits remain the reproducibility anchors.

## Empirical work delegated to #829

The following cannot be closed from upstream documentation and is explicitly owned by #829:

- representative model correctness through the platform-owned `ModelProvider` path;
- cold load and ready-to-first-request time;
- TTFT and ITL/TPOT distributions;
- request/token throughput at the campaign concurrency points;
- peak and steady VRAM/RAM;
- cancellation and in-flight server termination behavior;
- bounded OOM/resource-exhaustion behavior;
- restart/readiness/resource recovery;
- repeated start/stop leak checks;
- remote Worker loss and recovery;
- multi-GPU behavior on available target hardware;
- actual installation, configuration and upgrade burden on the target Worker;
- whether the same representative model revision/quantization is sufficiently equivalent across SGLang, vLLM and Ollama to permit a measured local-path comparison;
- the final `supported_optional`, `experimental_only`, or `reject/defer` classification.

This source-backed comparison therefore **does satisfy #860's documentation-side operational-comparison requirement**. It narrows #829's live test plan but does not substitute for real reference-host measurements or the final backend policy decision.
