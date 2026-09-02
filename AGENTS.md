# AGENTS.md

## Repository purpose

This repository contains the general-purpose AI Multi-Agent Platform. It is not the ScoreSymphony music application and must remain reusable by unrelated applications.

## Normative architecture baseline

All contributors and coding agents must treat the following documents as authoritative unless an explicit Architecture Decision Record changes a decision:

- [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) — product identity, canonical workflow, deployment/cost goals and replaceable architecture layers.
- [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md) — non-negotiable architecture principles and explicit invariants.
- [`docs/adr/README.md`](docs/adr/README.md) — process for recording material architecture decisions.

Implementation work must not silently contradict the normative documents. If a material decision changes canonical domain semantics, lifecycle ownership, public contracts, adapter boundaries, persistence ownership, distributed execution, security boundaries or replacement strategy, create or update an ADR and the affected normative documentation.

## Non-negotiable rules

1. Do not hard-code Hermes, Forge, LiteLLM, MCP, a model vendor, a VPS type or another concrete implementation into canonical platform contracts.
2. Add integrations behind platform-owned interfaces/adapters.
3. Keep canonical Task, Run, Agent, Artifact, Event, Node and Worker identities independent from backend-private identifiers.
4. Prefer capability-based selection over host/provider-specific branching.
5. The baseline must remain runnable without required recurring paid AI/API services.
6. Do not copy, vendor, fork, or selectively port third-party source before its provenance and license treatment are documented and compatible with `LICENSE_POLICY.md`.
7. For a new architecture-significant upstream, complete `docs/UPSTREAM_ADOPTION_CHECKLIST.md` and update `docs/UPSTREAMS.md` when it becomes approved/integrated.
8. When an upstream integration changes, update provenance metadata, review dates, compatibility constraints, notices, update method, and exit/replacement strategy as applicable.
9. Architecture-significant upstream updates must follow `docs/UPSTREAM_UPDATE_WORKFLOW.md`; never silently replace an upstream or redefine canonical contracts to match it.
10. Preserve required upstream copyright/license/NOTICE material and traceable origin information for copied or modified code.
11. Keep frontend and external clients on canonical platform APIs; do not connect them directly to implementation backends.
12. Add or update tests when changing lifecycle semantics, adapter contracts, or architecture-significant upstream behavior.
13. Architectural changes should reference the relevant GitHub issue and update authoritative documentation; use an ADR when an upstream forces a material architecture change.
14. Preserve both single-agent and multi-agent workloads behind the same canonical task/run model.
15. Treat single-node operation as a valid production topology and multi-node operation as an extension of the same contracts.
16. Treat security, approvals, traceability, observability and recovery as cross-cutting platform requirements.

## Third-party integration categories

Use the categories defined in `LICENSE_POLICY.md`: protocol/specification integration, library dependency, external self-hosted service, adapter integration, vendored source, forked source, selective code port, and reference-only influence.

A public or open-source repository is not automatically safe to copy. Prefer the least coupled integration category that satisfies the requirement.

## Initial implementation order

Follow the numbered GitHub issues. The first architecture sequence is product vision, repository baseline, upstream/license policy, canonical domain model, core interfaces, and then the platform task/run/event kernel.

## Source layout direction

- `src/ai_multi_agent_platform/` — platform-owned runtime and domain code
- `adapters/` — replaceable integrations
- `workers/` — reference and production worker implementations
- `frontend/` — web frontend
- `docs/` — authoritative architecture/product documentation
- `tests/` — unit, contract, integration and end-to-end tests
