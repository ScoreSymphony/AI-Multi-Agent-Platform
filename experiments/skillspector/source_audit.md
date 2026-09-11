# SkillSpector pinned source and data-egress audit (#800)

## Scope and pin

This audit covers only the exact upstream revision used by the #800 evaluation:

- project: `NVIDIA/SkillSpector`
- version: `v2.11.2`
- commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- declared license: Apache-2.0
- upstream package status: Alpha (`Development Status :: 3 - Alpha`)

Source-audit conclusions are not substitutes for executed benchmark evidence. Any later upstream
revision requires a new compatibility/security review before it can replace this pin.

## Static/no-LLM mode is not inherently offline

The pinned CLI supports `--no-llm`, but that flag disables semantic LLM analysis rather than all
network behavior. The supply-chain analyzer's SC4 path uses the OSV.dev API:

- `https://api.osv.dev/v1/querybatch`
- `https://api.osv.dev/v1/vulns/...`

The pinned `osv_client.py` implements bounded requests, an in-memory cache, and a static fallback
when OSV is unreachable. Therefore:

1. plain `--no-llm` may transmit dependency package names, ecosystems and versions to OSV.dev;
2. a successful no-LLM scan must not be described as offline unless network access was separately
   prohibited or observed absent;
3. #800 uses container `--network=none` for its canonical baseline so static evidence can be
   produced without external data egress;
4. OSV-enriched scanning, if later useful, must be a separately authorized mode whose network use
   is recorded in evidence.

This distinction is part of the evidence contract: `static_no_llm_network_none` and a future
`static_no_llm_osv` mode are not interchangeable results.

## LLM-assisted mode and possible egress

The pinned configuration supports HTTP-provider and CLI-provider paths. Source/configuration expose
at least:

- NVIDIA Build through `NVIDIA_INFERENCE_KEY`;
- OpenAI-compatible endpoints through `OPENAI_API_KEY` and `OPENAI_BASE_URL`;
- Anthropic through `ANTHROPIC_API_KEY`;
- Anthropic-compatible/proxy endpoints;
- agent CLI transports including Claude, Codex and Gemini where available;
- optional LangChain/LangSmith tracing configuration, with the example configuration setting
  `LANGCHAIN_TRACING_V2=false`.

`OPENAI_BASE_URL` is explicitly documented as supporting local/self-hosted OpenAI-compatible
endpoints such as Ollama or vLLM. That means SkillSpector does not inherently require a new paid API
for semantic analysis. It does **not**, however, make LLM mode safe by default: analyzer prompts can
contain Skill-derived metadata/code/context and are sent through the selected provider transport.

Platform policy must therefore treat LLM-assisted scanning as explicit data egress unless the
selected endpoint is an approved local boundary. The production default, if SkillSpector is ever
integrated, must remain LLM-disabled until #15/#34/#43 policy authorizes the provider, endpoint,
credential source and content classes that may leave the scanner boundary. Tracing must remain
disabled unless separately approved.

The #800 evaluation intentionally does not forward platform/API credentials into the static
container and does not execute an external LLM scan merely to satisfy a benchmark. That would turn a
security evaluation into an unauthorized content-egress path.

## MCP tool-poisoning coverage

Pinned upstream documentation divides MCP tool-poisoning checks into:

- TP1 hidden instructions: static;
- TP2 Unicode/homoglyph/deceptive metadata: static;
- TP3 parameter-description injection/default abuse: static;
- TP4 description/behavior mismatch: LLM-assisted only.

TP1-TP3 are described upstream as deterministic static checks with no LLM API call. TP4 is skipped
under `--no-llm`. The evaluation corpus therefore includes hidden metadata, a Cyrillic homoglyph and
parameter-description injection so the executed static benchmark can verify the advertised TP1-TP3
path instead of assuming documentation equals detection.

## Input resolution and scanner self-isolation

Pinned upstream can accept local paths as well as Git/URL/archive inputs. Git inputs use an external
clone process; URL inputs and dependency lookups can create network activity. Upstream also contains
explicit tests for archive/path-traversal and resource-bound behavior, including malicious ZIP member
names and nested-artifact preflight logic.

For the platform adapter this flexibility is unnecessary and increases attack surface. The #800 PoC
therefore narrows the contract:

1. canonical platform intake resolves source, version and provenance first;
2. the scanner receives only a staged local candidate snapshot;
3. symlinks in that staged input are rejected by the PoC;
4. the candidate is mounted read-only;
5. container root filesystem is read-only;
6. Linux capabilities are dropped and `no-new-privileges` is set;
7. process, memory and CPU limits are applied;
8. a bounded `tmpfs` is provided for scanner temporary state;
9. network is disabled for the baseline run;
10. scanned Skill code is never invoked by the adapter.

A future adapter should continue refusing Git URLs, arbitrary web URLs and unreviewed archive inputs
at the SkillSpector boundary. Canonical source intake should own fetching and provenance; the scanner
should inspect the resulting immutable snapshot.

## Code execution boundary

The advertised dangerous-code analysis is static: upstream includes Python AST checks for constructs
such as `exec`, `eval`, subprocesses and `os.system`, plus taint/data-flow analysis. These analyzers
inspect code; they are not a reason to execute candidate code.

The scanner itself of course executes its own parser/analyzer implementation and helper processes
for supported input-resolution paths. This is another reason to prefer an isolated CLI/container
adapter over importing SkillSpector into the platform process.

## Integration placement decision

For any follow-up implementation, preferred order is:

| Mode | Evaluation |
|---|---|
| CLI/container subprocess | Preferred: strongest replaceability, version pinning and failure boundary; JSON is sufficient for evidence ingestion. |
| In-process Python library | Not preferred: broad dependency coupling and scanner failure share the platform runtime. |
| MCP server | Not preferred for this use case: adds a service/tool trust surface without improving the pre-install evidence boundary. |

The scanner is an evidence provider, not a platform authority. No provider score, `safe_to_install`
field, severity or recommendation may mutate canonical trust state, Approval, installation or
activation directly.

## Failure and degradation semantics

The platform must preserve these distinctions:

- completed report, no findings -> clean **scanner evidence**, not trusted Skill;
- completed report, findings -> findings evidence;
- timeout/process crash -> degraded evidence;
- malformed/missing JSON -> degraded evidence;
- incomplete analysis -> degraded evidence;
- OSV unavailable in network-enabled mode -> explicit limitation/fallback, not silently equivalent
  to an OSV-complete result;
- LLM provider unavailable -> LLM-assisted evidence degraded; any separately completed static result
  remains separate evidence;
- newer scanner result -> new immutable evidence for that exact candidate revision; it must not
  rewrite an older trust decision.

The PoC normalizer intentionally has no trust/approval/install fields.

## Maintenance and replacement risk

The pinned package is explicitly classified Alpha and has a broad Python dependency surface,
including LLM/provider libraries even when the baseline uses `--no-llm`. Installing it in-process
would therefore create avoidable dependency and update coupling. Container pinning keeps those
transitive dependencies outside the platform Python environment and lets the provider be removed or
replaced without changing canonical Skill identity.

Any production adoption should pin both the upstream source revision and built-image digest, retain
raw-report digests, record scanner version/config/mode in `SecurityEvidence`, and re-run the
regression corpus before upgrading the pin.

## Source-audit conclusion

The pinned design is technically compatible with an **optional evidence-provider** role, especially
through its static JSON path, but source inspection alone is insufficient to recommend adoption.
The decisive remaining evidence is the executed fixture benchmark: detection coverage, false
positives, false negatives, stability, latency/resource behavior, offline degradation, and any
unexpected scanner failure states.

Until those executed results are reviewed, the recommendation remains `reject/defer` in the sense of
**defer production integration**, not rejection of the candidate. If the isolated benchmark shows
useful incremental coverage with acceptable noise and stable fail-closed behavior, the likely target
recommendation is `optional_evidence_provider`, never canonical trust authority.
