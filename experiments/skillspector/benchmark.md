# SkillSpector benchmark protocol (#800)

## Evaluation pin

| Item | Value |
|---|---|
| Upstream | `NVIDIA/SkillSpector` |
| Version | `v2.11.2` |
| Commit | `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc` |
| Baseline mode | `static_no_llm_network_none` |
| Output | JSON |

## Current evidence status

Source inspection confirms a static `--no-llm` CLI path and machine-readable JSON output.
The local execution environment used to prepare this PoC does not provide SkillSpector or a
container sandbox and cannot resolve GitHub for an install. A repository-local GitHub Actions
workflow now builds the exact pinned upstream source and executes the deterministic corpus inside
the hardened container path. Execution-derived values must come from that workflow artifact rather
than from source inspection.

The source audit also establishes that `--no-llm` alone is not synonymous with offline operation:
SC4 can query OSV.dev. The canonical baseline therefore additionally enforces `--network=none`.
See `source_audit.md` for the data-egress analysis.

This distinction is intentional: source-audit evidence is not benchmark evidence.

## Reproducible run protocol

1. Build a local evaluation image from the exact pinned upstream commit. Record the image
   identity and build inputs. Do not use an unpinned `latest` image.
2. Generate the deterministic corpus from `fixtures.py`.
3. Run each fixture at least three times in static/no-LLM mode with container networking
   disabled.
4. Record exit status, raw-report SHA-256, normalized evidence and wall time. Record bounded
   resource configuration and add measured resource telemetry where the runner exposes it.
5. Repeat with network enabled only under explicit evaluation authorization to identify
   dependency/vulnerability-service behavior; record every contacted service and transmitted
   metadata.
6. Evaluate LLM-assisted mode separately only with an explicitly authorized provider or approved
   local endpoint and document exactly which Skill content crosses that boundary.
7. Manually review every finding and every intentionally planted behavior. Record false positives,
   known false negatives, duplicates/noise and severity mismatches.
8. Run malformed, oversized, nested/archive and path/symlink cases before recommending production
   integration.

## Result table

The machine-readable workflow artifact is authoritative for executed results. This table remains
pending until a completed artifact is reviewed rather than pre-filling outcomes from expectations.

| Fixture class | Expected signal | Static result | Manual classification | Notes |
|---|---|---|---|---|
| benign | none | pending workflow | pending | benign control |
| benign legitimate shell | ideally none/review-level | pending workflow | pending | false-positive control |
| benign documentation code | ideally none/review-level | pending workflow | pending | code-snippet false-positive control |
| prompt injection | finding | pending workflow | pending | explicit override/persistence text |
| hidden prompt injection | finding | pending workflow | pending | HTML-comment metadata |
| parameter injection | finding | pending workflow | pending | MCP-style parameter description |
| memory poisoning | finding | pending workflow | pending | persistent-context instruction |
| exfiltration | finding | pending workflow | pending | env/file signal plus network intent |
| dangerous code | finding | pending workflow | pending | inert AST-dangerous constructs |
| MCP tool poisoning | finding | pending workflow | pending | hidden metadata + homoglyph + parameter injection |
| supply chain | finding/unknown | pending workflow | pending | synthetic typo/dependency cue |
| obfuscated | finding or documented FN | pending workflow | pending | base64 instruction override |
| mixed | finding | pending workflow | pending | benign task plus adversarial content |
| resource abuse | completed/degraded but bounded | pending workflow | pending | large benign parser input |

Do not convert this small corpus into a single accuracy percentage. Report class-level coverage and
concrete errors instead.

## Pinned CLI exit contract

For SkillSpector v2.11.2 the CLI distinguishes findings from scanner errors:

- exit `0`: scan completed and risk score did not cross the CLI failure threshold;
- exit `1`: scan completed but risk score exceeded the policy threshold; this is **not** by itself a
  scanner crash;
- exit `2`: scanner/input/runtime error.

A parsed report is usable only when its own execution/completeness fields also establish a completed
analysis. An exit code never overrides malformed or explicitly incomplete report content.

## Failure semantics

| Condition | Required normalized state |
|---|---|
| exit 0 + valid complete report, no findings | `clean`, `complete=true` |
| exit 0 or 1 + valid complete report with findings | `findings`, `complete=true` |
| exit 1 + malformed/incomplete report | `degraded`, never clean |
| exit 2 | `degraded`, never clean |
| timeout/process start failure | degraded evidence, never clean |
| malformed/missing JSON | degraded/invalid-report evidence, never clean |
| partial analysis | `degraded`, `complete=false` |
| OSV unavailable in an OSV-enabled mode | explicit limitation/fallback; never pretend OSV-complete |
| network disabled in canonical static baseline | expected; evidence mode records `network_none` |
| LLM provider unavailable | LLM scan degraded; static evidence remains separate |

## Adoption gates

Recommend `optional_evidence_provider` only if the pinned evaluation demonstrates useful
incremental coverage, acceptable noise, reproducible machine output, bounded resource use,
controllable data egress and safe failure semantics. `adopt` would require materially stronger
evidence and still must not grant SkillSpector trust authority. Recommend `reject/defer` if offline
behavior is unsuitable, scanner isolation is weak, noise is too high, important planted risks are
routinely missed, or dependency/update burden is unacceptable.

## Provisional recommendation

**`reject/defer` for production integration pending executed benchmark review.** The source/API
shape is promising enough to continue evaluation, especially the static JSON path, but source
inspection alone cannot justify integration. If the isolated benchmark demonstrates useful
incremental coverage with acceptable noise and stable failure behavior, the intended target is
`optional_evidence_provider`, not canonical trust authority.
