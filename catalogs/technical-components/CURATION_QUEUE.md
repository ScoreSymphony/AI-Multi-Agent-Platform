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

The loaded seed intentionally contains only reviewed entries. The names below remain queued even when they were already named in architecture documents: architecture mention is a reason to evaluate a component, not evidence that its current upstream identity, license and project status have been verified.

## Code intelligence / static analysis

- Serena
- ast-grep
- Semgrep
- SCIP

## Coding agents / workers

- OpenCode
- Goose
- Cline
- Roo Code
- Plandex
- jcode
- Kimi Code CLI
- MiMo Code
- ZCode
- Gemini CLI
- Copilot CLI
- Cursor Agent
- Kiro
- Claude Code
- Codex CLI
- mini-SWE-agent
- SWE-ReX

## Alternative agent / orchestration platforms

- Paperclip
- Agent Zero
- PAI / LifeOS
- Dify
- Flowise
- Sim Studio
- AnythingLLM
- Multica
- Open Agent
- Fable / Fabel
- Ruflo / Claude-Flow
- Gas Town
- Microsoft Agent Framework
- Agno
- CrewAI
- Langflow

## Specification / skills systems

- Superpowers
- ECC
- GSD
- OpenSpec
- BMAD Method

## Memory / context / retrieval

- OpenViking
- TurboVec
- Letta

## Evaluation / security

- Harbor
- OpenEnv
- AgentShield
- Inspect AI
- AgentDojo
- garak
- DeepEval

## Inference / model infrastructure

- Colibri
- Hugging Face tooling umbrella entry
- TEI
- ONNX Runtime

## Domain / music AI

- BACHI
- AnalysisGNN
- CLaMP 3
- MusicBERT
- MERT
- MuSViT
- LEGATO
- Transformers.js

## Discovery sources

- MCP Registry — discovery-source candidate only; must never become an automatically trusted install source.

## Intentionally absent from the default technical home surface

Ordinary SaaS productivity connectors such as Gmail, Google Calendar or Slack may remain valid generic Registry items, but they are not the technical Marketplace product identity and are not promoted through this queue merely to increase catalog size.
