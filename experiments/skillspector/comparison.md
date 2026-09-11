# SkillSpector versus the canonical platform baseline (#800)

This comparison separates **platform authority/policy controls** from **content-security detection**.
SkillSpector can only add evidence in the latter category; it must not replace the former.

## Existing platform baseline

The canonical Skill model from #588 already provides controls that a scanner cannot replace:

- stable `SkillDefinition` identity plus immutable `SkillRevision` history;
- exact source/revision/license/checksum/signature metadata for external Skills;
- server-resolved Capability requirements and compatibility checks;
- fail-closed resolution when a Skill is untrusted, disabled, incompatible, out of scope or requires
  unavailable capabilities/model properties;
- immutable `SkillBundle` hashes and historical trust evidence;
- an explicit third-party lifecycle from discovery through source verification, security review,
  isolated pilot/evaluation and adoption;
- external Skills enter disabled and cannot enable themselves;
- adoption requires a passed platform evaluation;
- portable imports do not silently transfer runtime trust between deployments;
- baseline operation does not require a hosted Skill service or paid AI/API dependency.

The platform security model additionally treats external/model/tool content as untrusted input,
keeps authorization and Approval platform-owned, records security-relevant decisions, and excludes
plaintext secrets from persistent audit/telemetry.

Repository search did not identify an existing dedicated Skill-content scanner that independently
covers prompt injection, code/data-flow analysis, MCP metadata poisoning and dependency/CVE signals.
That is the incremental niche #800 is evaluating.

## Capability comparison

| Area | Platform/manual baseline | SkillSpector v2.11.2 candidate | Architectural conclusion |
|---|---|---|---|
| Source/revision/license identity | Canonical and immutable in #588 | Scanner accepts input but is not canonical source authority | Platform remains authoritative |
| Trust state / adoption | Canonical lifecycle and evaluation gates | Produces scores/recommendations only | Never map scanner result directly to trust |
| Authorization / Approval | Canonical #15 boundary | Not authoritative | Platform-only decision |
| Capability least privilege | Server-resolved capabilities cannot be widened by a Skill | Can flag advertised MCP/permission patterns | Scanner evidence may supplement, not replace enforcement |
| Prompt injection | General untrusted-input policy/manual review; no dedicated Skill scanner located | Advertised static patterns plus optional semantic analysis | Potential incremental detection value; benchmark required |
| Hidden/encoded injection | No dedicated Skill-content detector located | TP/static pattern families advertise hidden/encoded detection | Benchmark required; known FNs must be explicit |
| Data exfiltration | Network/secret/capability policy and runtime boundaries | Static pattern + Python taint/data-flow checks | Complementary: detection before install, enforcement remains platform-owned |
| Dangerous code | Runtime/tool/capability policy; manual review | Python AST/static dangerous-code rules | Potentially useful pre-install evidence |
| MCP tool poisoning | MCP/tool trust remains a platform boundary | TP1-TP3 static; TP4 LLM-assisted at pinned revision | Useful specialty area if fixture results confirm coverage |
| Description/behavior mismatch | Manual/policy review | TP4 requires LLM-assisted mode | Not available in canonical offline static baseline |
| Supply-chain/CVE | Canonical source/provenance; dependency policy elsewhere | SC4 OSV.dev lookup with static fallback | Useful evidence, but no-LLM alone is not offline |
| Typosquatting | No Skill-specific detector located | Static package-name checks | Potential incremental value |
| False-positive control | Human review/evaluation | Baselines/suppression supported | Suppression remains provider evidence, not platform approval |
| Structured machine output | Platform resources/evidence are structured | JSON/Markdown/SARIF-style outputs | JSON fits a replaceable adapter |
| Offline/local operation | Required baseline property | Possible only when network is separately blocked; OSV otherwise may be contacted | Canonical mode must record `network_none` explicitly |
| External LLM data egress | Policy/Approval-controlled | Multiple hosted/local-compatible provider paths | LLM mode remains optional and separately authorized |
| Historical explainability | Immutable revisions/bundles/trust evidence | Upstream scoring/rules may change | Retain exact scanner revision, config, raw-report digest and mode |
| Failure semantics | Platform expected to fail closed | Scanner can crash/timeout/degrade | Adapter must turn failures into incomplete evidence, never clean pass |
| Runtime dependency coupling | Platform Python environment should remain stable | Broad Alpha-stage dependency graph | Prefer isolated CLI/container, not in-process import |
| Replaceability | Canonical Skill identity is provider-neutral | Scanner-specific rules/IDs | Keep provider IDs namespaced and adapter removable |
| MCP integration mode | MCP is an additional trust/tool surface | Upstream may expose MCP | No reason to use MCP for this pre-install scan path |
| Recurring paid dependency | Not acceptable for baseline operation | Static mode needs no paid LLM; semantic mode can use several providers/local-compatible endpoints | No new paid API may become mandatory |

## What SkillSpector must prove to add value

A clean architectural fit alone is insufficient. The executed corpus must demonstrate all of the
following before the candidate should move beyond `reject/defer` for production integration:

1. useful detection across at least prompt injection, exfiltration, dangerous code, MCP poisoning and
   supply-chain fixtures;
2. acceptable noise on benign Skills, including legitimate shell/code-documentation controls;
3. reproducible findings across repeated scans;
4. explicit false negatives rather than a misleading aggregate accuracy percentage;
5. bounded behavior on large/malformed inputs;
6. stable machine-readable reports and fail-closed handling of partial/error states;
7. useful incremental evidence beyond controls the platform already enforces canonically;
8. no mandatory external LLM/API or uncontrolled data-egress path.

## Preferred integration if the benchmark passes

The narrowest justified production shape is:

`canonical external-Skill intake -> immutable staged snapshot -> isolated SkillSpector CLI/container -> namespaced SecurityEvidence -> platform trust review`

The scanner should receive neither authority to fetch arbitrary sources on behalf of the platform nor
an ability to mutate Skill state. The platform should own source acquisition/provenance, policy,
Approval, capability enforcement, adoption and activation. SkillSpector should remain replaceable.
