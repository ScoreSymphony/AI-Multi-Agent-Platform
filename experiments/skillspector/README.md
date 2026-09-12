# SkillSpector evaluation (#800)

This directory contains the completed evaluation harness for NVIDIA SkillSpector as an **optional
pre-install security-evidence provider** for the canonical #588 Skill trust lifecycle. It is not a
production integration and does not grant the scanner trust authority.

## Pinned upstream

- Project: `NVIDIA/SkillSpector`
- Version: `v2.11.2`
- Commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- License: Apache-2.0
- Approved evaluation mode: `static_no_llm_network_none`

`--no-llm` alone is not equivalent to offline operation: the pinned supply-chain analyzer can query
OSV.dev. The approved baseline therefore also enforces container `--network=none`.

## Final #800 decision

**Recommendation: `optional_evidence_provider` for the static, network-isolated CLI/container path
only.**

The final `generated-corpus-v3` benchmark executed 15 fixtures three times each (**45 scans**).
Every scan produced runnable evidence and every fixture's semantic finding signature was stable
across repeats. The scanner demonstrated useful incremental coverage for prompt injection,
obfuscation, dangerous code, exfiltration, MCP poisoning/least privilege and supply-chain signals.

It is intentionally **not** recommended as canonical trust authority. The corpus also demonstrated:
- a benign negation false positive around credential-access wording;
- a complete static miss of the memory-poisoning fixture;
- incomplete file-read→network flow correlation;
- no dedicated persistence finding for the autostart fixture;
- overlapping/duplicate findings in several pattern families;
- partial/degraded supply-chain evidence when OSV is unreachable by policy.

See `benchmark.md` for the executed result table and exact artifact identity.

## Safety boundary

`runner.py`:
- rejects candidate symlinks;
- stages a local candidate copy;
- uses Docker/Podman by default;
- disables networking;
- mounts the candidate read-only;
- uses a read-only container root;
- drops Linux capabilities;
- sets `no-new-privileges`;
- bounds PIDs, memory, CPU and temporary storage;
- forwards only a small environment allowlist;
- refuses host-process execution unless explicitly requested for diagnostics.

The scanner output directory is a private temporary leaf made writable for the
capability-dropped container. Candidate code is inspected, never executed.

## Evidence boundary

`normalize.py` maps SkillSpector JSON into advisory `SecurityEvidence` carrying:
- candidate/revision/digest;
- scanner version/revision and scan mode;
- policy/config version and timestamp;
- provider occurrence IDs plus stable rule IDs;
- severity/confidence/path/line evidence;
- provider-native risk metadata;
- suppression data;
- completeness/degradation state;
- network/provider usage;
- raw-report digest.

It exposes no canonical trust, Approval, installation, enablement or activation mutation.

A scanner-native `SAFE`, score, severity or suppression therefore cannot bypass #588/#15/#43
source/license/capability/Approval/pilot/evaluation gates.

## Executed benchmark

`.github/workflows/skillspector-evaluation.yml` checks out the exact upstream revision, verifies its
license, builds the evaluation image, runs the harness regression suite and scans each fixture three
times. The final v3 evidence is workflow run `34657274640`, artifact id `10286034099`, digest
`sha256:5c3f7f39154d23a4a77151652e1eabf7ace68056b976f912877f2ebab3bdd760`.

`run_benchmark.py` records normalized evidence, raw report digests, semantic stability and wall-clock
latency. `benchmark.md` contains the reviewed classification and final recommendation.

## LLM-assisted mode

LLM-assisted scanning is a **separate, deferred mode**. Pinned upstream can place Skill-derived
content in provider prompts. #800 therefore did not send evaluation Skills to an external LLM
without explicit content-egress authorization.

No claim is made that LLM-assisted mode is production-ready. A future evaluation must use an
explicitly approved local/covered or otherwise authorized endpoint and record provider, egress,
latency, reproducibility and incremental detection value. It must never become mandatory for the
baseline.

## Production follow-up constraints

Any implementation following #800 must:
- remain optional and replaceable;
- preserve the isolated CLI/container boundary;
- pin source plus immutable built/dependency identity;
- default to `static_no_llm_network_none`;
- preserve fail-closed incomplete/degraded semantics;
- retain exact evidence provenance;
- keep canonical source acquisition, policy, Approval, trust and activation platform-owned;
- add no mandatory hosted service or recurring paid API.

`comparison.md` and `source_audit.md` record the architectural and data-egress rationale.
