# AGENTS.md

## Repository purpose

This repository contains the general-purpose AI Multi-Agent Platform. It is not the ScoreSymphony music application and must remain reusable by unrelated applications.

## Non-negotiable rules

1. Do not hard-code Hermes, Forge, LiteLLM, MCP, a model vendor, a VPS type or another concrete implementation into canonical platform contracts.
2. Add integrations behind platform-owned interfaces/adapters.
3. Keep canonical Task, Run, Agent, Artifact, Event, Node and Worker identities independent from backend-private identifiers.
4. Prefer capability-based selection over host/provider-specific branching.
5. The baseline must remain runnable without required recurring paid AI/API services.
6. Do not copy third-party source into the repository before its provenance and license treatment are documented.
7. Keep frontend and external clients on canonical platform APIs; do not connect them directly to implementation backends.
8. Add or update tests when changing lifecycle semantics or adapter contracts.
9. Architectural changes should reference the relevant GitHub issue and update authoritative documentation.

## Initial implementation order

Follow the numbered GitHub issues. The first architecture sequence is product vision, repository baseline, upstream/license policy, canonical domain model, core interfaces, and then the platform task/run/event kernel.

## Source layout direction

- `src/ai_multi_agent_platform/` — platform-owned runtime and domain code
- `adapters/` — replaceable integrations
- `workers/` — reference and production worker implementations
- `frontend/` — web frontend
- `docs/` — authoritative architecture/product documentation
- `tests/` — unit, contract, integration and end-to-end tests
