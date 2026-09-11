# SGLang vs vLLM operational comparison for issue #860

Status: **source-backed pre-measurement comparison**  
Issue: **#860**  
Observed: **2026-09-12**

This document records the operational facts that can be established before the live GPU campaign. It is deliberately separate from measured performance and recovery evidence. No throughput, latency, memory, startup, reload or reliability conclusion may be inferred from this document.

## Pinned comparison revisions

The primary GPU-serving comparison for this campaign is frozen to:

| Backend | Release | Commit | Published | License |
| --- | --- | --- | --- | --- |
| SGLang | `v0.5.19` | `0bcd822377da7b5718e674eaf9c870d349424dd1` | 2026-09-05 | Apache-2.0 |
| vLLM | `v0.29.0` | `98dff2a81d747d1dba01a47f939f48c3526d4206` | 2026-09-09 | Apache-2.0 |

Machine-readable provenance is stored in:

- `config/inference-backend-upstream.sglang-v0.5.19.json`;
- `config/inference-backend-upstream.vllm-v0.29.0.json`.

Measured reports using another SGLang or vLLM revision are not decision evidence for this campaign. A newer release requires either a new campaign or an explicit campaign revision rather than silently replacing the comparator.

## Source-backed operational surface

| Dimension | SGLang | vLLM | What #860 may conclude now |
| --- | --- | --- | --- |
| License | Apache-2.0 at pinned tag | Apache-2.0 at pinned tag | no license blocker identified for evaluation |
| Installation surface | upstream documentation exposes pip/uv, source and Docker installation paths | upstream installation docs and the `v0.29.0` GitHub release expose packaged installation paths, including platform-specific release wheels | both have self-hosted installation paths; maintenance effort is still unmeasured |
| Project target profile | #860 evaluates SGLang primarily as a GPU-serving backend on Linux Workers | vLLM is the pinned GPU-serving comparator | this campaign must not generalize a GPU result to CPU-only/VPS profiles |
| Hardware breadth in current upstream docs | current SGLang docs describe NVIDIA as the common path and separate AMD/CPU/TPU/XPU/other platform guides | current vLLM installation docs list NVIDIA CUDA, AMD ROCm, Intel XPU, Apple Silicon and multiple CPU architectures | documentation breadth is capability-discovery evidence, not proof that the representative model behaves equivalently on every platform |
| Single-node multi-GPU | SGLang documents tensor/data parallel serving controls | vLLM documents tensor/pipeline/data/expert parallel controls | live Worker evidence is still required |
| Multi-node serving | SGLang documents `--nnodes`, `--node-rank` and `--dist-init-addr` | vLLM documents multi-node serving, including node/rank controls and Ray/multiprocessing deployment paths | topology exists upstream for both; platform-owned remote Worker behavior remains unverified |
| OpenAI-compatible serving | documented by SGLang and covered by the #860 contract plan | vLLM provides an OpenAI-compatible serving path used as the comparator | wire compatibility alone is insufficient; canonical `ModelProvider` behavior must be measured |
| Release packaging observed on GitHub | SGLang `v0.5.19` release has no attached GitHub release assets; upstream docs point to package/Docker distribution channels | vLLM `v0.29.0` release publishes platform-specific wheel assets, including CPU/CUDA/XPU variants | distribution mechanics differ; actual install/upgrade burden on the target Worker must be recorded during the campaign |
| Operational failure/recovery | documented native health/observability features are evidence inputs only | native server/distributed controls are evidence inputs only | neither backend receives a reliability advantage until #860 observes canonical health/routing recovery |

## Evidence sources

Pinned revision/license evidence:

- SGLang release: <https://github.com/sgl-project/sglang/releases/tag/v0.5.19>
- SGLang tag/license provenance: `config/inference-backend-upstream.sglang-v0.5.19.json`
- vLLM release: <https://github.com/vllm-project/vllm/releases/tag/v0.29.0>
- vLLM tag/license provenance: `config/inference-backend-upstream.vllm-v0.29.0.json`

Operational capability discovery observed on 2026-09-12:

- SGLang installation: <https://docs.sglang.io/docs/get-started/install>
- SGLang multi-node/server arguments: <https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/server_arguments.md>
- vLLM installation: <https://docs.vllm.ai/en/stable/getting_started/installation/>
- vLLM serving CLI: <https://docs.vllm.ai/en/stable/cli/serve/>
- vLLM multi-node example: <https://docs.vllm.ai/en/stable/examples/ray_serving/multi-node-serving/>

The documentation links above are discovery evidence observed at the evaluation date. Where they point to moving `main`/`stable` documentation, they do not replace the exact release commits as reproducibility anchors.

## What remains genuinely empirical

The following cannot be closed from upstream documentation and remains mandatory live evidence:

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
- multi-GPU behavior on the available target hardware;
- actual installation, configuration and upgrade burden on the target Worker.

Therefore this comparison narrows the live test plan but does not satisfy the final #860 outcome gate by itself.
