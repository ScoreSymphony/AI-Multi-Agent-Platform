# SkillSpector pinned source and data-egress audit (#800)

## Scope and pin

This audit covers the exact revision evaluated by #800:
- project: `NVIDIA/SkillSpector`
- version: `v2.11.2`
- commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- declared license: Apache-2.0
- package status: Alpha (`Development Status :: 3 - Alpha`)

Executed evidence is bound to workflow run `34657274640` and artifact id `10286034099`
(`sha256:5c3f7f39154d23a4a77151652e1eabf7ace68056b976f912877f2ebab3bdd760`).

Any later upstream revision requires a new compatibility/security review before replacing this pin.

## Static/no-LLM mode is not inherently offline

`--no-llm` disables semantic LLM analysis, not all networking. The pinned supply-chain SC4 path can
use OSV.dev:
- `https://api.osv.dev/v1/querybatch`
- `https://api.osv.dev/v1/vulns/...`

The client has bounded requests/cache/static fallback. Therefore:
1. plain `--no-llm` may send package names/ecosystems/versions to OSV.dev;
2. a no-LLM scan must not be called offline unless networking is independently prohibited;
3. the canonical #800 baseline uses container `--network=none`;
4. OSV-enriched scanning, if later useful, must be a distinct authorized mode whose network use is
   recorded in evidence.

The executed supply-chain fixture confirms that network-off OSV behavior is represented as
partial/degraded evidence rather than a false clean pass.

## LLM-assisted mode and possible egress

Pinned configuration supports HTTP-provider and CLI-provider paths, including NVIDIA Build,
OpenAI-compatible endpoints, Anthropic-compatible paths and agent CLI transports. It also supports
OpenAI-compatible local/self-hosted endpoints.

That flexibility does not make semantic mode safe by default: Skill-derived metadata/code/context
may enter analyzer prompts and therefore cross the selected provider boundary. Optional tracing is
another data path and must remain disabled unless separately approved.

Platform policy must treat LLM-assisted scanning as explicit data egress unless the endpoint is an
approved local boundary. Provider, endpoint, credential source, content classes and tracing behavior
must be governed through #15/#34/#43.

#800 did not forward platform/API credentials and did **not** send Skill content to an external LLM
merely to satisfy evaluation completeness. No external LLM mode is approved by the #800 decision.
No claim is made about semantic-mode detection uplift or reproducibility until such a mode is
separately authorized and measured.

## MCP tool-poisoning coverage

Pinned upstream separates:
- TP1 hidden instructions: static;
- TP2 Unicode/homoglyph/deceptive metadata: static;
- TP3 parameter-description/default abuse: static;
- TP4 description/behavior mismatch: LLM-assisted.

The executed static corpus detected the planted TP1-TP3/homoglyph/parameter patterns. TP4 remains
outside the approved static baseline.

## Input resolution and scanner self-isolation

Pinned upstream can accept local paths, Git/URL/archive inputs. Those broader resolution paths can
clone/fetch and increase attack surface. The platform adapter deliberately narrows the contract:
1. canonical intake resolves source/version/provenance;
2. scanner receives only a staged local snapshot;
3. candidate symlinks are rejected;
4. candidate mount is read-only;
5. container root filesystem is read-only;
6. capabilities are dropped and `no-new-privileges` is set;
7. PID/memory/CPU limits are applied;
8. bounded tmpfs is used for temporary state;
9. baseline network is disabled;
10. candidate Skill code is never invoked by the adapter;
11. only a minimal environment allowlist reaches the scanner.

The writable scanner-output leaf is deliberately restricted to the private temporary evaluation
tree. On POSIX it is mode `0733`, sufficient for the capability-dropped container to create output
without making the surrounding private temporary directory traversable to unrelated users.

A production adapter should continue refusing Git URLs, arbitrary web URLs and unreviewed archive
inputs at the scanner boundary. Canonical intake should own acquisition and provenance.

## Code execution boundary

Dangerous-code analysis is static. The executed corpus showed detection for eval, subprocess,
`os.system`, privilege language and destructive filesystem behavior without executing candidate
code. The scanner itself still executes parser/analyzer code, which is why process/container
isolation is preferable to importing it into the platform process.

## Integration placement decision

| Mode | Decision |
|---|---|
| CLI/container subprocess | **Preferred**: strongest replaceability, source/image pinning and failure boundary; JSON is sufficient. |
| In-process Python library | Not preferred: broad dependency coupling and scanner failure share the platform runtime. |
| MCP server | Not preferred for this pre-install path: adds a service/tool trust surface without necessary benefit. |
| LLM-assisted provider path | Deferred/not approved by #800; requires separate egress authorization and evaluation. |

## Failure and degradation semantics

The platform preserves:
- completed report, no findings -> clean **scanner evidence**, not trusted Skill;
- completed report, findings -> findings evidence;
- timeout/process crash -> degraded/incomplete evidence;
- malformed/missing JSON -> degraded/invalid evidence;
- incomplete analysis -> degraded evidence;
- OSV unavailable -> explicit partial/fallback state;
- LLM provider unavailable -> any future LLM evidence degraded; static evidence stays separate;
- newer scan -> new evidence for that exact candidate revision, never a rewrite of historical trust.

The PoC normalizer intentionally has no trust/approval/install/enable fields.

## Reproducibility and dependency risk

The source revision is exact, but the upstream package declares a broad set of ranged transitive
dependencies, including LLM/provider libraries even when `--no-llm` is used. The executed artifact
therefore also records the installed dependency set hash.

A production implementation should:
- pin exact upstream source revision;
- build and retain an immutable image/artifact identity;
- lock or otherwise reproduce the transitive dependency set;
- retain raw-report digest plus scanner/config/mode identity;
- re-run the regression corpus before changing the provider revision or dependency lock.

This matters especially because the upstream package is Alpha.

## Final source/security conclusion

Source inspection plus the executed corpus justify **`optional_evidence_provider`**, narrowly for
the static `static_no_llm_network_none` CLI/container path.

The scanner is useful but not authoritative: the benchmark contains a real benign false positive,
a complete miss of the memory-poisoning fixture, a file-flow correlation gap and no dedicated
persistence finding for the autostart fixture. Those limitations rule out treating provider scores
or `SAFE` recommendations as trust decisions.

The selected integration shape remains replaceable, local/offline at the scanner boundary and free
of any mandatory paid API. LLM-assisted mode remains separately policy-gated and deferred.
