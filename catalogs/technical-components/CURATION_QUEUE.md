# Technical Marketplace curation queue

This queue is deliberately **not** consumed by `FilesystemRegistryProvider`. It records architecture-derived and externally researched leads that have not yet satisfied the complete source/license/project-status review required for promotion into the loaded technical catalog.

A queue entry is not trusted, installable, adopted, rejected or even necessarily the correct upstream identity. It is a research lead only.

## Promotion gate

Before moving a name from this queue into the loaded catalog, a reviewer must resolve and record:

1. exact official upstream identity and canonical source URL;
2. current license from the official upstream source;
3. current project status, including archived/deprecated/maintenance-only state where applicable;
4. an appropriate canonical Registry item type and technical category/categories;
5. discovery-only/manual distribution unless a real canonical platform package exists;
6. lifecycle and evaluation state;
7. deployment, cost, network/provider and resource facts only where evidence exists;
8. security/capability implications and benchmark requirements;
9. overlap/alternatives and architecture/evaluation/decision references where useful.

Unknown facts stay unknown. Popularity, stars, vendor claims or a Marketplace listing never substitute for evaluation or adoption governance.

## Loaded promotion batches

### 2026-09-09 initial expansion

The first #638 expansion resolved and loaded the following architecture-derived and externally researched candidates:

- Code intelligence: Serena, ast-grep, Semgrep and SCIP. SCIP records the canonical `scip-code/scip` namespace rather than the former redirected Sourcegraph URL.
- Coding agents: OpenCode, Goose, Cline, Roo Code, Plandex, Gemini CLI and Codex CLI. OpenCode and Goose record their current post-move namespaces. Roo Code is explicitly an archived `reference`, not an active candidate.
- Agent platforms/frameworks: Dify, Flowise, AnythingLLM, Microsoft Agent Framework, Agno and CrewAI. Dify preserves its modified Apache-2.0 terms; Flowise is an archived reference with mixed Apache/commercial licensing recorded explicitly.
- Memory/context: Letta.
- Evaluation/security: Inspect AI, AgentDojo, garak and DeepEval.
- Browser/execution: Stagehand.
- Inference/model infrastructure: TEI and ONNX Runtime.
- Domain/model tooling: Transformers.js.

### 2026-09-10 second verified promotion wave

The following names completed source/license/project-status review and are loaded through deterministic `catalog.fragment.*.json` files alongside the primary `catalog.json`:

#### Coding agents / workers

- jcode — `cnjack/jcode`, MIT, non-archived.
- Kimi Code CLI — canonical project `MoonshotAI/kimi-code`, MIT, non-archived.
- MiMo Code — `XiaomiMiMo/MiMo-Code`, MIT, non-archived.
- ZCode — `RenovZ/zcode`, MIT, non-archived.
- mini-SWE-agent — `SWE-agent/mini-swe-agent`, MIT, non-archived.
- SWE-ReX — `SWE-agent/SWE-ReX`, MIT, non-archived; modeled as browser/execution infrastructure rather than a coding-agent identity.

#### Alternative agent / orchestration platforms

- Paperclip — `paperclipai/paperclip`, MIT, non-archived.
- Agent Zero — `agent0ai/agent-zero`, non-archived; repository-root license is MIT even though GitHub's detector reported `NOASSERTION` during review.
- PAI / LifeOS — canonical repository `danielmiessler/LifeOS`, MIT, non-archived.
- Sim Studio — canonical repository `simstudioai/sim`, Apache-2.0, non-archived.
- Ruflo / Claude-Flow — canonical project `ruvnet/ruflo`, MIT, non-archived; the former Claude-Flow naming is not modeled as a separate current component.
- Gas Town — canonical repository `gastownhall/gastown`, MIT, non-archived.
- Langflow — `langflow-ai/langflow`, MIT, non-archived.

#### Specification / skills systems

- Superpowers — `obra/superpowers`, MIT, non-archived.
- ECC — canonical repository `affaan-m/ECC`, MIT, non-archived.
- OpenSpec — `Fission-AI/OpenSpec`, MIT, non-archived.
- BMAD Method — canonical organization repository `bmad-code-org/BMAD-METHOD`, non-archived; root license is MIT with trademark/branding notices even though GitHub's detector reported `NOASSERTION` during review.

#### Memory / context / retrieval

- OpenViking — `volcengine/OpenViking`, AGPL-3.0, non-archived. Copyleft review remains explicit in catalog metadata.

#### Evaluation / security

- Harbor — `harbor-framework/harbor`, Apache-2.0, non-archived.
- OpenEnv — `huggingface/OpenEnv`, BSD-3-Clause, non-archived.

#### Domain / music AI

- BACHI — official implementation `AndyWeasley2004/BACHI_Chord_Recognition`, MIT, non-archived.
- AnalysisGNN — `manoskary/analysisgnn`, MIT, non-archived.
- CLaMP 3 — `sanderwood/clamp3`, MIT, non-archived.
- MERT — `yizhilll/MERT`, Apache-2.0, non-archived.

Promotion still means discovery/evaluation only. All active additions remain `manual`, `untrusted`, `candidate`, and `evaluation:required`; no third-party executable code is bundled. Facts not established by review remain explicit `unknown` values.

## Reviewed but requires restricted/reference semantics

These entries have enough evidence to classify, but must not be presented as ordinary free/open active candidates:

- Copilot CLI — `github/copilot-cli`, non-archived, governed by the custom GitHub Copilot CLI license rather than an open-source license. The license permits install/run and limited unmodified redistribution but does not grant a general modification/derivative-work right. GitHub service access/cost is a separate requirement. Promote only with explicit restricted-license and cost-policy metadata, likely `deferred` or `reference` under the project's cost policy.
- Claude Code — `anthropics/claude-code`, non-archived, but its repository license states all rights reserved and use is subject to Anthropic commercial terms. It must not be represented as open source or cost-compatible; any listing should be `reference`/`deferred` unless project governance changes.
- Multica — `multica-ai/multica`, non-archived, but its "Multica License" is Apache-2.0 plus additional hosted-service, branding and attribution restrictions. It must use explicit modified-license metadata rather than plain `Apache-2.0`.
- GSD — canonical `gsd-build/get-shit-done`, MIT, but GitHub reports the repository archived. Preserve only as an explicit archived `reference` unless governance selects a documented successor rather than silently substituting a fork.
- MuSViT — `OMR-PRAIG-UA-ES/MuSViT`, non-archived, but the project README licenses the work under CC BY-NC-SA 4.0 and requires GPU-backed experiment paths. It should be `reference`/`deferred` for commercial/general platform adoption unless that non-commercial restriction is acceptable for the intended domain use.

## Reviewed identity, but dependency/license model still needs one more pass

- MusicBERT — the architecture lead resolves to the MusicBERT implementation under Microsoft's broader `microsoft/muzic` project. Before promotion, record whether the catalog card represents the subproject or umbrella repository and verify the applicable subproject/model-data license boundary.
- LEGATO — code identity resolves to `guang-yng/legato`, but the practical model path also depends on gated Meta Llama 3.2 Vision assets and materially higher GPU resources. The code license alone is insufficient for a truthful cost/provider/license card.

## Truly unresolved / ambiguous leads

These still lack an unambiguous current identity, suitable component boundary, or complete licensing/status evidence and must stay out of the loaded catalog:

### Coding agents / workers

- Cursor Agent — proprietary product identity is clear, but there is no canonical open repository/package identity suitable for the current local reference-catalog contract.
- Kiro — product identity is clear, but the catalog still needs a canonical source/project reference and licensing/cost model appropriate to the Registry metadata contract.

### Alternative agent / orchestration platforms

- Open Agent — name is too ambiguous to select a canonical upstream safely.
- Fable / Fabel — architecture label is ambiguous; exact intended upstream has not been resolved.

### Memory / context / retrieval

- TurboVec — exact intended upstream identity remains unresolved; a plausible current repository is not enough to prove it is the architecture-intended component.

### Evaluation / security

- AgentShield — multiple active projects use the exact name for materially different security products. The architecture mention alone does not identify which upstream was intended.

### Inference / model infrastructure

- Colibri — exact intended inference/model project remains ambiguous.
- Hugging Face tooling umbrella entry — this is an ecosystem umbrella rather than one canonical distributable component. Keep it out until a deliberate aggregate/reference-card model is defined; individual Hugging Face projects such as TEI, Sentence Transformers, smolagents, Lighteval and Transformers.js are modeled separately.

### Discovery sources

- MCP Registry — the generic discovery seam already exists, but an MCP Registry listing/import source must remain discovery-only and can never become an automatically trusted install source. A concrete adapter is optional follow-up work rather than a prerequisite to preserve #638's trust boundary.

## Intentionally absent from the default technical home surface

Ordinary SaaS productivity connectors such as Gmail, Google Calendar or Slack may remain valid generic Registry items, but they are not the technical Marketplace product identity and are not promoted through this queue merely to increase catalog size.

## Completion boundary

The Marketplace engine, technical taxonomy, graphical presentation, manual/reference routing and discovery-source seam are implemented. Remaining entries above are **curation evidence and classification work**, not missing Registry execution/install code.

A queued name can be resolved by:

1. promotion as a reviewed manual candidate;
2. explicit archived/restricted `reference` or `deferred` classification;
3. explicit rejection when licensing/security/cost policy makes it unsuitable; or
4. documenting that no sufficiently unambiguous/current upstream identity exists.

No name should be silently dropped, guessed or bulk-promoted merely to make issue #638 appear numerically complete.
