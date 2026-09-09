# Technical Marketplace curation queue

This queue is deliberately **not** consumed by `FilesystemRegistryProvider`. It records architecture-derived and externally researched leads that have not yet satisfied the complete source/license/project-status review required for promotion into `catalog.json`.

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

## 2026-09-09 verified promotion batch

The following queue leads were resolved against their current official upstream identity, license and GitHub project status and are now represented in the loaded catalog:

- Code intelligence: Serena, ast-grep, Semgrep and SCIP. SCIP now records the canonical `scip-code/scip` namespace rather than the former redirected Sourcegraph URL.
- Coding agents: OpenCode, Goose, Cline, Roo Code, Plandex, Gemini CLI and Codex CLI. OpenCode and Goose record their current post-move namespaces. Roo Code is explicitly an archived `reference`, not an active candidate.
- Agent platforms/frameworks: Dify, Flowise, AnythingLLM, Microsoft Agent Framework, Agno and CrewAI. Dify preserves its modified Apache-2.0 terms; Flowise is an archived reference with mixed Apache/commercial licensing recorded explicitly.
- Memory/context: Letta.
- Evaluation/security: Inspect AI, AgentDojo, garak and DeepEval.
- Browser/execution: Stagehand.
- Inference/model infrastructure: TEI and ONNX Runtime.
- Domain/model tooling: Transformers.js.

Promotion still means discovery/evaluation only. All active additions remain `manual`, `untrusted`, `candidate`, and `evaluation:required`; archived entries remain `manual`, `untrusted`, `reference`, and non-activatable.

## Remaining unresolved leads

These names remain queued because their exact current identity, license/status, product boundary, or suitability still needs a separate evidence pass. They must not be bulk-promoted merely to make the catalog numerically complete.

### Coding agents / workers

- jcode
- Kimi Code CLI
- MiMo Code
- ZCode
- Copilot CLI
- Cursor Agent
- Kiro
- Claude Code
- mini-SWE-agent
- SWE-ReX

### Alternative agent / orchestration platforms

- Paperclip
- Agent Zero
- PAI / LifeOS
- Sim Studio
- Multica
- Open Agent
- Fable / Fabel
- Ruflo / Claude-Flow
- Gas Town
- Langflow

### Specification / skills systems

- Superpowers
- ECC
- GSD
- OpenSpec
- BMAD Method

### Memory / context / retrieval

- OpenViking
- TurboVec

### Evaluation / security

- Harbor
- OpenEnv
- AgentShield

### Inference / model infrastructure

- Colibri
- Hugging Face tooling umbrella entry

### Domain / music AI

- BACHI
- AnalysisGNN
- CLaMP 3
- MusicBERT
- MERT
- MuSViT
- LEGATO

### Discovery sources

- MCP Registry — discovery-source candidate only; must never become an automatically trusted install source.

## Intentionally absent from the default technical home surface

Ordinary SaaS productivity connectors such as Gmail, Google Calendar or Slack may remain valid generic Registry items, but they are not the technical Marketplace product identity and are not promoted through this queue merely to increase catalog size.

## Completion boundary

The Marketplace engine, technical taxonomy, graphical presentation, manual/reference routing and discovery-source seam are already implemented. Remaining items in this file are **curation evidence work**, not missing Registry execution/install code. A queued name can be resolved by promotion, explicit reference/deferred/rejected classification, or by documenting that no sufficiently unambiguous/current upstream identity exists.
