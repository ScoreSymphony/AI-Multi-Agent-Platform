# SkillSpector evaluation (#800)

This directory is an **evaluation harness**, not a production integration. It evaluates
NVIDIA SkillSpector as an optional producer of pre-install security evidence for the
canonical Skill trust lifecycle from #588.

## Pinned upstream

- Project: `NVIDIA/SkillSpector`
- Version: `v2.11.2`
- Commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- License: Apache-2.0 (verify again before any production adoption)

The pinned upstream CLI supports static scans with `--no-llm` and JSON output. The source
audit for this evaluation therefore treats static/no-LLM scanning as the baseline. LLM
analysis is a separate mode because it changes data-egress, reproducibility, cost and
policy properties.

## Safety boundary

`runner.py` stages a copy of a candidate into a temporary directory and prefers Docker or
Podman with networking disabled, a read-only root filesystem, dropped capabilities,
`no-new-privileges`, resource limits and a read-only candidate mount. It refuses host
execution by default. `--allow-local-process` exists only for explicit diagnostic use and
must not be treated as a security boundary.

The harness passes only a small environment allowlist to the scanner. It never reads or
forwards platform secrets intentionally. Input fixtures are non-destructive and contain no
live credentials.

The harness does **not** make trust decisions. A SkillSpector score, `safe_to_install`
value, severity or recommendation remains provider-native metadata. `normalize.py` maps a
report to advisory evidence only. Scanner failure or an incomplete scan becomes degraded
evidence, never a clean pass.

## Deterministic corpus

`fixtures.py` creates benign plus synthetic prompt-injection, exfiltration,
dangerous-code, MCP tool-poisoning, supply-chain and obfuscation fixtures. The malicious
examples are inert documentation/pseudocode and are not intended to execute.

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

## What must still be measured

The source audit alone cannot establish detection quality. `benchmark.md` records the
reproducible benchmark plan and deliberately leaves execution-derived values unclaimed
until the pinned scanner is run in an isolated environment.

Before production adoption, record at minimum false positives, known false negatives,
repeated-run stability, latency/resource use, behavior with network disabled, dependency
vulnerability lookups, malformed/resource-abuse inputs, LLM-mode egress and the exact raw
report schema. Any production adapter must remain replaceable and platform policy must
remain authoritative for trust, approval, installation and activation.
