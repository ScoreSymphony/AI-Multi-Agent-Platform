# SGLang, vLLM and Ollama operational comparison for issue #860

Status: **source-backed pre-measurement comparison**  
Issue: **#860**  
Observed: **2026-09-12**

This document records the operational facts that can be established before the live GPU campaign. It is deliberately separate from measured performance and recovery evidence. No throughput, latency, memory, startup, reload or reliability conclusion may be inferred from this document.

## Pinned comparison revisions

The campaign is frozen to one candidate, one GPU-serving comparator and one lighter local-path comparator:

| Backend | Role | Release | Commit | License |
| --- | --- | --- | --- | --- |
| SGLang | candidate | `v0.5.19` | `0bcd822377da7b5718e674eaf9c870d349424dd1` | Apache-2.0 |
| vLLM | GPU-serving comparator | `v0.29.0` | `98dff2a81d747d1dba01a47f939f48c3526d4206` | Apache-2.0 |
| Ollama | lighter local-path comparator | `v0.34.0` | `d8ab4b4f0ca24b51d3a46b3bf4f462e58ce66b1f` | MIT |

Machine-readable provenance is stored in:

- `config/inference-backend-upstream.sglang-v0.5.19.json`;
- `config/inference-backend-upstream.vllm-v0.29.0.json`;
- `config/inference-backend-upstream.ollama-v0.34.0.json`.

Measured reports using another pinned backend revision are not evidence for this campaign. A newer release requires either a new campaign or an explicit campaign revision rather than silently replacing a comparator. llama.cpp remains a valid future/local-path candidate, but it is not mixed with Ollama under one ambiguous report identity in this campaign.

## Source-backed operational surface

| Dimension | SGLang | vLLM | Ollama | What #860 may conclude now |
| --- | --- | --- | --- | --- |
| License | Apache-2.0 at pinned tag | Apache-2.0 at pinned tag | MIT at pinned tag | no license blocker identified for evaluation |
| Installation surface | upstream documentation exposes pip/uv, source and Docker installation paths | upstream installation docs and the `v0.29.0` GitHub release expose packaged installation paths, including platform-specific release wheels | `v0.34.0` publishes install scripts plus Linux/macOS platform archives, including ROCm-specific Linux assets | all three have self-hosted distribution paths; actual maintenance effort is still unmeasured |
| Project target profile | #860 evaluates SGLang primarily as a GPU-serving backend on Linux Workers | pinned GPU-serving comparator | pinned lighter local-path comparator | a GPU-serving result must not be generalized to every local/CPU/VPS profile |
| Hardware breadth visible in current upstream/release evidence | current SGLang docs describe NVIDIA as the common path and separate AMD/CPU/TPU/XPU/other platform guides | current vLLM installation docs list NVIDIA CUDA, AMD ROCm, Intel XPU, Apple Silicon and multiple CPU architectures | pinned release assets include Linux AMD64/ARM64 and macOS packages, with dedicated ROCm/JetPack variants among the Linux assets | packaging/capability breadth is discovery evidence, not model-level compatibility proof |
| Single-node multi-GPU | SGLang documents tensor/data parallel serving controls | vLLM documents tensor/pipeline/data/expert parallel controls | not a required distributed-serving comparator for #860 | live Worker evidence is still required for SGLang and vLLM |
| Multi-node serving | SGLang documents `--nnodes`, `--node-rank` and `--dist-init-addr` | vLLM documents multi-node serving, including node/rank controls and Ray/multiprocessing deployment paths | outside the purpose of the lighter local-path comparator | topology exists upstream for the GPU-serving pair; platform-owned remote Worker behavior remains unverified |
| OpenAI-compatible serving | documented by SGLang and covered by the #860 contract plan | vLLM provides an OpenAI-compatible serving path used as the GPU comparator | campaign uses the OpenAI-compatible path only where the pinned Ollama surface supports the requested operation | wire compatibility alone is insufficient; canonical `ModelProvider` behavior must be measured |
| Release packaging observed on GitHub | SGLang `v0.5.19` release has no attached GitHub release assets; upstream docs point to package/Docker distribution channels | vLLM `v0.29.0` publishes platform-specific wheel assets, including CPU/CUDA/XPU variants | Ollama `v0.34.0` publishes installers and packaged platform archives | distribution mechanics differ; actual install/upgrade burden on target Workers must be recorded during the campaign |
| Operational failure/recovery | native health/observability features are evidence inputs only | native server/distributed controls are evidence inputs only | local server lifecycle is evidence input only | no backend receives a reliability advantage until #860 observes canonical health/routing recovery |

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

The documentation links above are discovery evidence observed at the evaluation date. Where they point to moving `main`/`stable` documentation, they do not replace the exact release commits as reproducibility anchors.

## What remains genuinely empirical

The following cannot be closed from upstream documentation and remains live evidence:

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
- actual installation, configuration and upgrade burden on the target Worker;
- whether the same representative model revision/quantization is sufficiently equivalent across SGLang, vLLM and Ollama to permit a measured local-path comparison.

Therefore this source-backed comparison narrows the live test plan and satisfies only the documentation side of the operational comparison. It does not satisfy the final #860 outcome gate by itself.
