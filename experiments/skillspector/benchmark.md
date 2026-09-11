# SkillSpector benchmark protocol (#800)

## Evaluation pin

| Item | Value |
|---|---|
| Upstream | `NVIDIA/SkillSpector` |
| Version | `v2.11.2` |
| Commit | `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc` |
| Baseline mode | static / `--no-llm` |
| Output | JSON |

## Current evidence status

Source inspection confirms a static `--no-llm` CLI path and machine-readable JSON output.
The evaluation environment used to prepare this PoC does **not** currently provide
SkillSpector, Docker, Podman or bubblewrap. Therefore no detection-rate, latency, memory or
false-positive numbers are claimed here. Those values must come from an actual run of the
pinned revision in the isolated harness.

This distinction is intentional: source-audit evidence is not benchmark evidence.

## Reproducible run protocol

1. Build a local evaluation image from the exact pinned upstream commit. Record the image
   digest and build inputs. Do not use an unpinned `latest` image.
2. Generate the deterministic corpus from `fixtures.py`.
3. Run each fixture at least three times in static/no-LLM mode with container networking
   disabled.
4. Record exit status, raw-report SHA-256, normalized evidence, wall time, peak memory and
   CPU where available.
5. Repeat with network enabled only to identify dependency/vulnerability-service behavior;
   record every contacted service and transmitted metadata.
6. Evaluate LLM-assisted mode separately only with an explicitly authorized provider and
   document exactly which Skill content leaves the platform.
7. Manually review every finding and every intentionally planted behavior. Record false
   positives, known false negatives, duplicates/noise and severity mismatches.
8. Run malformed, oversized, nested/archive and path/symlink cases before recommending
   integration.

## Result table

| Fixture class | Expected signal | Static result | Manual classification | Notes |
|---|---|---|---|---|
| benign | none | not run | pending | benign control |
| prompt injection | finding | not run | pending | explicit override/persistence text |
| exfiltration | finding | not run | pending | env/file -> network pseudocode |
| dangerous code | finding | not run | pending | documentation-only shell payload |
| MCP tool poisoning | finding | not run | pending | malicious metadata text |
| supply chain | finding/unknown | not run | pending | synthetic typo/dependency cue |
| obfuscated | finding or documented FN | not run | pending | base64 instruction override |

Do not convert this small corpus into a single accuracy percentage. Report class-level
coverage and concrete errors instead.

## Failure semantics

| Condition | Required normalized state |
|---|---|
| clean completed scan | `clean`, `complete=true` |
| completed scan with findings | `findings`, `complete=true` |
| non-zero scanner exit | `degraded`, never clean |
| timeout | degraded/timeout evidence, never clean |
| malformed/missing JSON | degraded/invalid-report evidence, never clean |
| partial analysis | `degraded`, `complete=false` |
| required network unavailable | explicit degraded/unknown result |
| LLM provider unavailable | LLM scan degraded; static evidence remains separate |

## Adoption gates

Recommend `optional_evidence_provider` only if the pinned evaluation demonstrates useful
incremental coverage, acceptable noise, reproducible machine output, bounded resource use,
controllable data egress and safe failure semantics. `adopt` would require materially
stronger evidence and still must not grant SkillSpector trust authority. Recommend
`reject/defer` if offline behavior is unsuitable, scanner isolation is weak, noise is too
high, important planted risks are routinely missed, or dependency/update burden is
unacceptable.

## Provisional recommendation

**`defer` pending executed benchmark.** The source/API shape is promising enough to keep the
candidate under evaluation, especially the no-LLM JSON path, but source inspection alone
cannot justify integration. No production dependency should be added by #800 until the
isolated benchmark and data-egress review have been executed and reviewed.
