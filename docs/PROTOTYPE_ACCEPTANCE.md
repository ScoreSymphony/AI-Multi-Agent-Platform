# Single-node usable-prototype acceptance gate

This document defines the release gate for the usable single-node prototype. The gate does not create a second platform stack. It runs focused canonical integration evidence owned by the subsystems that implement the acceptance criteria and emits one versioned report per profile.

## Command

From a repository checkout with the development and frontend dependencies installed:

```text
platform-acceptance --profile reference --json-report acceptance-reference.json
platform-acceptance --profile local-ai --json-report acceptance-local-ai.json
platform-acceptance --profile degraded --json-report acceptance-degraded.json
platform-acceptance --profile persistence --json-report acceptance-persistence.json
```

A failed check makes the command exit non-zero. The terminal summary names the owning issue/subsystem. The JSON report uses schema identifier `ai-multi-agent-platform/prototype-acceptance/v1` and contains no credentials or secret material.

## Profiles

### A — reference

Uses the deterministic single-node reference path and canonical subsystem tests. It proves the authenticated Task/Run/Result path, observable capability execution, exact-action approval boundaries, independent runtime verification and Memory/Knowledge lifecycle behavior.

The reference profile also binds CLI and Web contract evidence to the same `frontend/src/api/__fixtures__/canonical-task.json` snapshot. The Python CLI client must decode that snapshot through `/api/v1/tasks/{id}`, while the frontend parity test must read the same snapshot through the same versioned resource path. Neither client is allowed to invent a private Task representation.

For Memory, the acceptance-specific check performs the complete required sequence: create a user-owned entry with provenance, retrieve/query it, delete it and verify that a subsequent retrieval fails canonically with `NOT_FOUND`.

### B — local AI

Starts an ephemeral loopback HTTP server implementing only the OpenAI-compatible surface required by the replaceable reference adapter. The production standard-library HTTP transport discovers `/models` and performs `/chat/completions` with a canonical `ModelRequest`. No paid service, external network, Ollama, LM Studio or LiteLLM installation is required.

The profile also reruns the first-run onboarding model suite so provider-neutral local/self-hosted configuration, SecretReference handling and no implicit paid-provider selection remain part of the gate.

### C — degraded

Exercises first-run readiness when a configured provider or provider-native model becomes unavailable. The platform must remain in an actionable `needs_model` state rather than producing false readiness; canonical health revalidation is the recovery path.

### D — persistence

Reconstructs the single-node/reference services against durable state and checks canonical execution, Memory, Verification and first-run Task/Run/Result persistence.

## Release checklist and evidence map

| Criterion | Gate evidence | Owner |
| --- | --- | --- |
| authenticated clean single-node Control Plane / first administrator | reference single-node smoke | Deployment / Authentication |
| usable local/self-hosted model without paid service | `local-ai` loopback OpenAI-compatible check + model onboarding | Models / Onboarding |
| bounded canonical Task/Run/Result execution | reference single-node smoke | Deployment / Kernel |
| observable capability/tool evidence | reference single-node smoke | Capabilities / Observability |
| high-risk action requires exact approval | `tests/integration/security/test_authorization_final_boundaries.py` + `tests/unit/authorization/test_approval_project_scope.py` + `tests/contract/security/test_control_plane_authorization_vocabulary.py` | Authorization |
| verification independently gates concrete completion | `tests/integration/kernel/test_kernel_gate.py` | Verification |
| Memory create/retrieve/provenance/delete/not-found lifecycle | exact Memory acceptance test | Data / Prototype acceptance |
| Knowledge registration/update/ingest/reindex/removal lifecycle | `tests/integration/memory/test_memory_lifecycle_commands.py` + `tests/integration/knowledge/test_knowledge_lifecycle_commands.py` | Data |
| canonical state survives restart | persistence profile | Deployment / Onboarding / Data / Verification |
| CLI and Web consume the same canonical Task snapshot/path | shared Task fixture + Python CLI parity + frontend parity | Web / CLI / Prototype acceptance |
| unavailable provider/component fails closed and actionably | degraded profile | Onboarding / degraded-mode handling |
| no paid provider, fixed model product or fixed hardware dependency | local-AI fixture + onboarding policy tests | Models / Onboarding |
| no `docker exec`, direct database edit or private backend in the acceptance path | registry contract test + canonical subsystem boundaries | Prototype acceptance |

## CI

`.github/workflows/prototype-acceptance.yml` runs all four profiles independently on pull requests and `main`, and also runs nightly. `fail-fast` is disabled so one failed domain does not hide the remaining evidence. Each matrix job uploads its machine-readable JSON report even when a check fails.

## Failure ownership

The gate is intentionally an aggregator. A failed check reports both a stable check ID and the owning issue/subsystem. Fix the canonical subsystem or its integration evidence; do not add acceptance-only bypasses or frontend/private-backend shortcuts to make the gate green.

## Release rule

A usable-prototype release candidate is acceptable only when all required profiles are green on the candidate commit and their JSON reports are retained with the release evidence. Optional external providers may have additional compatibility tests, but they cannot replace the free local/reference acceptance path.
