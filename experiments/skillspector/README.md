# SkillSpector evaluation (#800)

This directory is an **evaluation harness**, not a production integration. It evaluates
NVIDIA SkillSpector as an optional producer of pre-install security evidence for the
canonical Skill trust lifecycle from #588.

## Pinned upstream

- Project: `NVIDIA/SkillSpector`
- Version: `v2.11.2`
- Commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- License: Apache-2.0

The pinned upstream CLI supports static scans with `--no-llm` and JSON output. `--no-llm`
does **not** by itself mean offline: the pinned supply-chain SC4 path can query OSV.dev and
fall back when unavailable. The canonical #800 baseline therefore combines `--no-llm` with
container `--network=none` and records the mode as `static_no_llm_network_none`.

LLM analysis is a separate mode because it changes data-egress, reproducibility, cost and
policy properties. `source_audit.md` records the pinned provider/network behavior and the
integration boundary.

The pinned JSON contract exposes `issues`, `risk_assessment`, `execution_successful` and
`analysis_completeness`. A CLI exit code of `1` can represent a policy-relevant risk score,
not a scanner crash. The harness therefore treats a parsed, complete report with
`execution_successful: true` as usable evidence for exit `0` or `1`. Exit `2`, malformed
output, missing required report structure or incomplete analysis is never normalized into a
clean result.

## Safety boundary

`runner.py` rejects candidate symlinks, stages a copy of a candidate into a temporary
directory and prefers Docker or Podman with networking disabled, a read-only root
filesystem, dropped capabilities, `no-new-privileges`, resource limits, a bounded temporary
filesystem and a read-only candidate mount. It refuses host execution by default.
`--allow-local-process` exists only for explicit diagnostic use and must not be treated as a
security boundary.

The harness passes only a small environment allowlist to the scanner. It does not forward
common platform/API credential variables. Input fixtures are non-destructive and contain no
live credentials.

The adapter contract deliberately supplies a **local staged snapshot**, not a Git URL or
arbitrary web URL. Canonical platform intake owns fetching/source/version/provenance; the
scanner only inspects that snapshot.

The harness does **not** make trust decisions. A SkillSpector score, severity or
recommendation remains provider-native metadata. `normalize.py` maps a report to advisory
evidence only. Scanner failure, malformed output or an incomplete scan becomes degraded
evidence, never a clean pass.

## Deterministic corpus

`fixtures.py` creates benign controls plus synthetic prompt-injection, hidden/parameter
injection, memory-poisoning, exfiltration, dangerous-code, MCP tool-poisoning, supply-chain,
obfuscation, mixed-content and resource-abuse fixtures. Dangerous examples are inert static
analysis inputs and contain no live credentials.

Example corpus generation:

```bash
python -c "from pathlib import Path; from experiments.skillspector.fixtures import write_fixture_corpus; write_fixture_corpus(Path('/tmp/skillspector-eval'))"
```

Example scan, once the pinned evaluation image exists:

```bash
python experiments/skillspector/runner.py /tmp/skillspector-eval/benign
```

The default command expects a locally built image named `skillspector-eval:2.11.2`; the
image must itself be built from the pinned upstream revision. The harness does not pull
images automatically because an evaluation of network behavior must not silently add a
network fetch.

## Executed benchmark path

`.github/workflows/skillspector-evaluation.yml` checks out the exact upstream commit, verifies
the pin/license, builds the evaluation image, runs the harness regression tests and executes
each deterministic fixture three times under the network-disabled baseline. Raw reports and
machine-readable summaries are retained as a workflow artifact.

`run_benchmark.py` records individual results, normalized evidence, report/finding stability
and wall-clock latency. `benchmark.md` is updated only from reviewed execution evidence; it
must not infer detection performance from source inspection.

## Remaining adoption gates

Before production adoption, review at minimum false positives, known false negatives,
repeated-run stability, latency/resource behavior, dependency-vulnerability degradation,
malformed/resource-abuse handling, LLM-mode data egress and the exact raw report schema.
External LLM execution must not be performed merely to complete this evaluation unless the
provider/endpoint and Skill-content egress have first been explicitly authorized.

Any production adapter must remain replaceable and platform policy must remain authoritative
for trust, Approval, installation and activation.
