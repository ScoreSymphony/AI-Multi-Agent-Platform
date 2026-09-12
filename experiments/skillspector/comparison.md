# SkillSpector versus the canonical platform baseline (#800)

This comparison separates **platform authority/policy controls** from **content-security
detection**. The executed `generated-corpus-v3` benchmark supports SkillSpector only as an optional
evidence producer; it does not replace canonical platform controls.

## Existing platform baseline

The canonical Skill model from #588 already owns:
- stable `SkillDefinition` identity and immutable `SkillRevision` history;
- exact source/revision/license/checksum/signature metadata;
- server-resolved Capability requirements and compatibility checks;
- fail-closed resolution for untrusted/disabled/incompatible/out-of-scope Skills;
- immutable bundle/trust evidence;
- source verification, security review, isolated pilot/evaluation and adoption lifecycle;
- disabled-by-default external Skills;
- platform-owned Approval/authorization and activation;
- portable provenance without silently transferring runtime trust.

SkillSpector's incremental niche is **content-security evidence before install**.

## Executed comparison

| Area | Platform/manual baseline | SkillSpector v2.11.2 evidence | Conclusion |
|---|---|---|---|
| Source/revision/license identity | Canonical #588 ownership | Scanner consumes a staged candidate; it is not source authority | Platform remains authoritative |
| Trust/adoption | Canonical lifecycle/evaluation | Emits scores/recommendations only | Never map directly to trust |
| Authorization/Approval | Canonical #15 boundary | Not authoritative | Platform-only decision |
| Capability least privilege | Server-side capability enforcement | `LP3` identified an undeclared tool scope in the legitimate-network control | Useful advisory evidence, not enforcement |
| Explicit prompt injection | General untrusted-input/manual review | `P1`/`YR4` detected 3/3 | Incremental value demonstrated |
| Hidden injection | No dedicated Skill-content scanner located | Hidden-comment fixture detected 3/3 with `TP1/P1/P2/YR4` | Incremental value demonstrated |
| Encoded/obfuscated injection | No dedicated Skill-content scanner located | Base64 fixture detected 3/3 | Incremental value demonstrated |
| Persistent memory poisoning | Platform policy/runtime memory boundary | Static fixture missed 3/3 | Known static false negative |
| Data exfiltration | Network/secret/capability enforcement | Env→network, credential/config access and callback detected; file→network flow not fully linked as `TT4` | Complementary, incomplete static evidence |
| Dangerous code | Runtime/tool/capability policy | eval/subprocess/os.system/sudo/destructive filesystem detected | Strong pre-install evidence |
| Persistence | Runtime/host policy | autostart write not separately detected | Known gap |
| MCP tool poisoning | MCP/tool trust boundary | TP1-TP3/homoglyph/parameter injection detected | Strong specialty evidence |
| Description/behavior mismatch | Manual/policy review | TP4 requires LLM-assisted mode | Not available in approved static baseline |
| Supply-chain/CVE | Canonical provenance/dependency policy | Typosquat detected; OSV network-off fallback becomes partial/degraded | Useful but network enrichment is separate mode |
| Benign shell/doc controls | Manual review | Both clean | Low noise for these controls |
| Benign negation | Manual semantics | `Do not ... access credentials` falsely triggers `PE3` | Concrete FP; human/platform review required |
| Benign network control | Capability/policy review | `LP3` because tool scope is undeclared; no exfiltration finding | Policy signal, not network-use FP |
| Large/repetitive input | Resource policy | Completes in ~14.95 s; `P9` repetition heuristic | Bounded but heuristic noise exists |
| Finding stability | Canonical evidence history | 15/15 fixture semantic signatures stable across 3 runs | Good reproducibility in static mode |
| Structured output | Platform-owned resource models | JSON output normalized into advisory `SecurityEvidence` | Good adapter fit |
| Offline/local operation | Baseline requirement | Achieved only with explicit `--network=none`; `--no-llm` alone can contact OSV | Record scan mode exactly |
| External LLM egress | Approval/policy-controlled | Upstream can send Skill-derived content through configured provider | Not approved as baseline |
| Runtime dependency coupling | Platform Python env should remain stable | Alpha package with broad/ranged dependency graph | Prefer pinned isolated container |
| Replaceability | Canonical identity provider-neutral | Provider rule/occurrence IDs remain namespaced | Keep adapter removable |
| MCP integration mode | MCP adds service/tool trust surface | No benefit over CLI for this path | Do not use MCP here |
| Recurring paid dependency | Not acceptable for baseline | Static network-none mode needs none | Meets project cost constraint |

## Integration placement

Preferred production seam:

`canonical external-Skill intake -> immutable staged snapshot -> isolated SkillSpector CLI/container -> namespaced SecurityEvidence -> platform trust review`

The scanner must not:
- fetch arbitrary Git/URL inputs on behalf of canonical intake;
- receive broad platform credentials;
- mutate Skill state;
- convert `SAFE`, risk score, severity or suppression into Approval;
- make external LLM use mandatory.

## Decision

The benchmark moves the candidate from provisional `reject/defer` to
**`optional_evidence_provider` for the static network-isolated mode only**.

`adopt` is intentionally not selected because the executed corpus contains a real false positive,
known false negatives/gaps and overlapping findings. The platform should use the scanner as one
evidence source alongside canonical provenance, capability policy, human review and other future
security evidence.

LLM-assisted scanning remains a separate, deferred mode until a provider/endpoint and content-egress
policy are explicitly authorized and independently evaluated.
