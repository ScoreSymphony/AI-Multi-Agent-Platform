# SkillSpector executed benchmark and decision (#800)

## Evaluation identity

| Item | Executed value |
|---|---|
| Upstream | `NVIDIA/SkillSpector` |
| Version | `v2.11.2` |
| Commit | `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc` |
| License | Apache-2.0 |
| License SHA-256 | `9f8785b47596b2993a17a3fa8d747ae63126a2c5e80a9e77195a907273d71839` |
| Upstream Dockerfile SHA-256 | `124041bd2c81880747197f221c8d9a13b7378ac5ad98a17b4ab6a15ad22eb9aa` |
| Corpus | `generated-corpus-v3` |
| Mode | `static_no_llm_network_none` |
| Repetitions | 3 per fixture |
| Fixtures | 15 |
| Executed scans | 45 |
| Workflow run | `34657274640` / run #35 |
| Artifact | `skillspector-issue-800-evidence`, id `10286034099` |
| Artifact digest | `sha256:5c3f7f39154d23a4a77151652e1eabf7ace68056b976f912877f2ebab3bdd760` |
| Built image ID | `sha256:55abb78a1f1af1f430722920b96d4e24bed03e9e9811e1cf5330b859d2387fc1` |
| Installed dependency-set SHA-256 | `d6716d890040ac73494a8422ea51ef11229e71e0c0d9b538a95316a3a644fc61` |

The workflow checked out the exact upstream revision, verified the license, built the candidate
image, ran the platform-side regression tests, then scanned every generated fixture three times
inside the hardened `--network=none` container path. Raw reports, normalized evidence and summary
files are retained in the workflow artifact.

## Aggregate execution results

All **45/45** scans produced runnable scanner results. All 15 fixtures had identical semantic
finding signatures across their three repetitions after excluding SkillSpector's intentionally
per-run finding occurrence UUID. Risk score/severity/recommendation was also stable for every
fixture. Raw report SHA-256 values differ between repetitions because reports contain dynamic
timestamps and occurrence IDs; byte-identical raw JSON is therefore not a meaningful stability
criterion.

Wall-clock scan time across all 45 runs was approximately **223.9 s**. The overall median was
approximately **4.27 s** per scan. Ordinary fixtures clustered around 4.2-4.3 s; the intentionally
large resource-abuse fixture averaged approximately **14.95 s** and remained bounded by the
configured container limits. The benchmark records wall time, not peak RSS/CPU telemetry, so no
stronger resource-efficiency claim is made.

## Fixture results

| Fixture | Static result (3/3 stable) | Manual classification |
|---|---|---|
| `benign` | `PE3`, score 17 / LOW / SAFE | **False positive:** negated text `Do not ... access credentials` is still flagged as credential access. |
| `benign-documentation-code` | clean, score 0 | Benign control passes; documented subprocess code is not treated as executable behavior. |
| `benign-legitimate-shell` | clean, score 0 | Benign shell control passes. |
| `benign-legitimate-network` | `LP3`, score 7 / LOW / SAFE | Not an exfiltration false positive. The Skill performs legitimate network access but omits an `allowed-tools` declaration, so the least-privilege finding is materially justified. |
| `prompt-injection` | `P1`, `YR4`, score 40 / MEDIUM | True positive for explicit instruction override/system-prompt extraction. |
| `prompt-injection-hidden` | `TP1`, `P1`, `P2`, `YR4`, score 91 / CRITICAL | True positive for hidden HTML-comment instructions. |
| `prompt-injection-parameter` | `P1`, `TP3`, `E4`, `YR4`, score 74 / HIGH | True positive for parameter-description injection/context exfiltration language. |
| `obfuscated` | `TP1`, `YR4`, score 38 / MEDIUM | True positive for the Base64-encoded instruction-override fixture. |
| `memory-poisoning` | clean, score 0 | **Known false negative:** persistent-memory/context poisoning fixture is not detected in static mode. |
| `exfiltration` | 7 findings including `TT3`, `PE3`, `E1`; score 100 / CRITICAL | Strong detection of environment→network flow, credential/config file access and hidden callback. **Known gap:** the SSH-key/`.env` file reads are flagged, but are not linked through to the POST as a dedicated `TT4` file-read→network flow. |
| `dangerous-code` | 8 findings including `AST2`, `AST4`, `AST5`, `SC2`, `TM1`, `PE2`; score 100 / CRITICAL | Detects eval, subprocess/curl-pipe-shell, `os.system`, `sudo` and destructive filesystem behavior. **Known gap:** the autostart persistence write does not receive a dedicated persistence finding. |
| `mcp-tool-poisoning` | 10 findings including `TP1`, `TP2`, `TP3`, `P1`, `P2`, `E4`; score 100 / CRITICAL | Strong static coverage for hidden metadata, homoglyph deception and parameter injection. Some YARA/pattern findings overlap semantically, so reviewer-visible deduplication remains desirable. |
| `supply-chain` | `AE1`, `SC6`, `SC4`; score 47 / MEDIUM; **degraded/partial** | Typosquat is detected. With network disabled, OSV enrichment falls back and completeness drops to 50%; importantly this is not normalized as a clean pass. |
| `mixed` | `P1`, `YR4`, score 40 / MEDIUM | True positive inside otherwise benign content. |
| `resource-abuse` | `P9`, score 8 / LOW / SAFE; ~14.95 s mean | Bounded completion. Repeated benign filler triggers a medium repetition/padding heuristic; treat this as review noise rather than malicious proof. |

No single accuracy percentage is reported because the corpus is deliberately small and
class-oriented. The useful result is the concrete coverage/noise boundary above.

## False positives, false negatives and noise

Observed false positive:
- `PE3` on a benign negated instruction that explicitly says **not** to access credentials.

Observed/known false negatives or detection gaps:
- static memory-poisoning/persistent-context fixture is missed completely;
- sensitive file reads are detected, but the combined file-read→network data flow is not expressed
  as a dedicated `TT4` finding in this fixture;
- the autostart persistence write does not receive a dedicated persistence finding.

Observed noise/duplication:
- some YARA and static prompt-injection findings overlap;
- `SC2` can produce more than one finding around the same `curl | sh` construct;
- large repeated benign text triggers `P9`, which is useful as an abuse heuristic but not as proof
  that the Skill is malicious.

These limitations are why a scanner-native score or recommendation must remain evidence rather than
canonical trust state.

## Offline and degradation behavior

`--no-llm` alone is not offline. Pinned SkillSpector can query OSV.dev for dependency
vulnerability data. The canonical benchmark therefore also uses `--network=none` and records the
mode as `static_no_llm_network_none`.

The supply-chain fixture demonstrates the desired fail-closed behavior under that restriction:
SkillSpector emits a fallback/partial analysis and the adapter normalizes the result to
`degraded`, `complete=false`; it is never converted into a clean pass.

The adapter regression suite additionally covers scanner process failure, timeout, malformed or
missing output, incomplete reports, unsupported non-directory input, schema-shape mismatch and
candidate symlinks. These states cannot normalize to `clean`.

## Static mode versus LLM-assisted mode

### Executed and approved baseline: static/no-LLM/network-none

This is the only mode recommended by #800 for implementation:
- no external LLM/API is required;
- candidate content is staged locally and mounted read-only;
- network is disabled;
- provider/API credential variables are not forwarded;
- JSON output is sufficient for a replaceable evidence adapter;
- scanner failure and partial output fail closed.

### LLM-assisted mode: evaluated architecturally, not authorized for execution

Pinned upstream supports hosted and OpenAI-compatible/local provider paths. Source inspection shows
that Skill-derived metadata/code/context can enter semantic-analysis prompts. Executing that mode
against an external provider would therefore be a distinct data-egress decision.

#800 intentionally did **not** transmit Skill content to an external LLM merely to complete a
benchmark. No approved provider/endpoint and content-egress authorization was established for this
evaluation. Consequently:
- no claim is made about LLM-assisted detection uplift, latency or reproducibility;
- LLM-assisted scanning is **not** part of the recommended production baseline;
- a future LLM-assisted evaluation requires an explicitly approved local or otherwise authorized
  endpoint plus #15/#34/#43 policy coverage;
- baseline operation remains free of any new recurring paid API/service.

This is a deliberate security boundary, not missing static-benchmark evidence.

## Evidence and trust boundary

The PoC records candidate identity/revision/digest, provider version/revision, mode, policy version,
timestamp, stable rule IDs, provider occurrence IDs, severity/confidence/path/line evidence,
provider-native risk metadata, suppression metadata, completeness/degradation, network/provider
usage and raw-report digest.

It intentionally exposes no field that can approve, trust, install, enable or activate a Skill.
A clean SkillSpector result therefore cannot bypass source/license verification, capability review,
Approval, isolated pilot or platform evaluation from #588/#15/#43.

## Final recommendation

**`optional_evidence_provider`**, narrowly scoped to the pinned static
`static_no_llm_network_none` CLI/container path.

Rationale:
- the scanner adds useful pre-install coverage for prompt injection, obfuscation, dangerous code,
  exfiltration, MCP poisoning/least-privilege and supply-chain signals that the canonical trust
  lifecycle does not itself specialize in detecting;
- JSON output and the isolated process boundary fit a replaceable evidence-provider seam;
- the benchmark demonstrates deterministic semantic findings and explicit degraded-state handling;
- baseline use requires no external LLM and no new recurring paid service;
- measured false positives, false negatives and duplicate/noisy findings make broad `adopt` or
  scanner-owned trust decisions unjustified.

Production implementation, if pursued, must remain opt-in/replaceable, retain the exact provider
revision/config/raw digest, pin an immutable built artifact/dependency set, preserve fail-closed
degradation, and leave all canonical trust/Approval/install/enable decisions to the platform.

LLM-assisted scanning is **deferred/not approved** by this decision and requires a separate,
explicitly authorized evaluation before it can become a supported mode.
