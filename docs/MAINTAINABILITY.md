# Maintainability signals and oversized-module review

Status: repository quality policy and review guide for #896. This extends the responsibility-first decomposition completed in #723; it does not replace [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) or change canonical ownership.

## Purpose

Module size and function complexity are diagnostic signals. They do not prove that code is badly structured, and a refactor is not complete merely because a file becomes shorter. The repository uses these signals to find places where unrelated responsibilities, difficult review surfaces or weak test seams may be accumulating.

The policy is intentionally asymmetric:

- historical outliers remain visible in generated reports;
- review thresholds are informational;
- CI rejects only **newly introduced unexempt extreme outliers**;
- an existing large module is not forced through a mechanical split merely to make unrelated PRs green.

This lets #896 reduce existing hotspots incrementally without converting historical debt into a repository-wide merge freeze.

## Reproducible inventory

Run:

```bash
python scripts/ci/maintainability_guard.py inventory \
  --root . \
  --config config/maintainability.toml \
  --json-out .maintainability/current.json \
  --markdown-out .maintainability/current.md
```

The inventory scans production Python under `src/ai_multi_agent_platform/` and records:

- module line count;
- function/method line span, including decorators;
- deterministic branch-complexity score;
- review/extreme classification;
- explicit exemption reason when one exists.

Branch complexity starts at 1 and adds decision points for conditionals, loops, exception handlers, boolean branches, match cases and comprehensions. Nested functions are measured independently rather than inflating their enclosing function.

The JSON output is the machine-readable evidence. The Markdown output ranks the largest modules, largest functions and highest-complexity functions for review.

## Thresholds

The checked policy lives in [`../config/maintainability.toml`](../config/maintainability.toml). Current signals are:

| Signal | Review | Extreme |
| --- | ---: | ---: |
| Production module lines | >400 | >1000 |
| Function/method lines | >50 | >100 |
| Function/method branch complexity | >12 | >20 |

These values are review heuristics, not canonical architecture constraints. Responsibility cohesion, dependency direction and stable contracts remain authoritative.

## Baseline snapshot

The first #896 inventory generated on 2026-09-15 for PR #1035 measured:

| Metric | Count |
| --- | ---: |
| Production Python modules | 984 |
| Modules above the review signal | 280 |
| Modules above the extreme signal | 7 |
| Functions/methods | 13,125 |
| Functions above the review signal | 940 |
| Functions above the extreme signal | 224 |

The seven current module-level extremes are:

| Lines | Module |
| ---: | --- |
| 1,136 | `learning/governed_learning_workflow.py` |
| 1,125 | `adapters/hermes.py` |
| 1,080 | `agents/matching.py` |
| 1,067 | `security/policy_profiles.py` |
| 1,047 | `browser/reference.py` |
| 1,036 | `context/source_adapters.py` |
| 1,017 | `onboarding/first_run_service.py` |

The largest individual function in that baseline is `deployment.single_node.build_single_node_deployment` at 511 lines. The highest measured branch complexity is `PlanningProposalValidator.validate` at 69. These are separate signals from module size and should be reviewed even when their containing module is below the module-level extreme threshold.

This snapshot is historical evidence, not a checked golden file. The generated CI inventory is authoritative for the current tree and is expected to improve as #896 refactors land.

## CI delta guard

For pull requests, repository-quality CI builds inventories for both the PR base commit and the PR head with the same checked policy, then compares them. CI fails when the head introduces a module or function that is extreme where the corresponding base item was not already an active extreme.

Existing extreme items remain reported but do not fail an unrelated PR. A new extreme must therefore be handled in one of two ways:

1. split or simplify the responsibility before merge; or
2. add a narrow explicit exemption with a reason when the code is intentionally generated or declarative.

Exemptions are exact. Module exemptions identify a path. Function exemptions identify both a path and qualified symbol. Broad directory globs and reason-free exemptions are intentionally unsupported.

## Responsibility-first refactor rules

The #723 invariants remain in force:

- preserve canonical domain ownership and supported public façades;
- prefer focused modules under the existing top-level package owner;
- do not create circular imports to reduce a line count;
- do not replace a monolith with generic `utils.py`, `helpers.py` or `common.py` dumping grounds;
- keep protocol/client, mapping, persistence, policy, error and transport seams separate when they are genuinely independent responsibilities;
- preserve restart, retry, ordering, cancellation, authorization and fail-closed semantics through behavior-level regression tests;
- avoid mixing unrelated feature changes into decomposition PRs.

## Prioritized audit queue

The generated inventory is authoritative for exact line/complexity rankings. The first module-level cohort should review all seven current extremes, but ranking alone does not determine refactor order. Priority also reflects responsibility density, public-contract risk and the availability of stable seams:

1. `adapters/hermes.py` — 1,125 lines mixing configuration/schema, HTTP transport, orchestration lifecycle, planning parsing, provider-error behavior and agent/team mapping. It has unusually clear seams and broad adapter-contract coverage, so it is the preferred first decomposition while preserving `ai_multi_agent_platform.adapters.hermes` imports.
2. `learning/governed_learning_workflow.py` — 1,136 lines, currently the largest production module. Audit workflow coordination, policy/governance decisions, persistence and result projection separately before choosing split points.
3. `security/policy_profiles.py` — 1,067 lines. Audit policy profile domain/service/compiler responsibilities against the already separate persistence layer before movement; fail-closed semantics must remain explicit.
4. `context/source_adapters.py` — 1,036 lines. Multiple source-specific collection paths make adapter-per-responsibility seams plausible, but canonical context ownership and classification/egress rules must remain centralized.
5. `browser/reference.py` — 1,047 lines. Audit reference-browser state/session behavior, command execution, capability registration and provider mechanics while keeping browser contracts stable.
6. `agents/matching.py` — 1,080 lines. Review matching constraints, scoring/ranking and compatibility diagnostics separately; avoid scattering the matching model across generic helpers.
7. `onboarding/first_run_service.py` — 1,017 lines. Review status projection, model setup, first-run orchestration and persistence seams while preserving idempotent onboarding behavior.

After the module-level extremes, the function-level report should drive the next cohort. In particular, very large composition/build functions and high-complexity validation/scheduling functions may offer more maintainability value than splitting a merely large file.

Large adapter-family modules (`adapters/litellm.py`, `adapters/mcp*.py`), `security/egress_profiles.py`, verification modules and context lifecycle/operational modules remain review candidates below the current 1,000-line extreme signal. They should be split only where protocol/client/mapping/lifecycle or policy/persistence responsibilities are genuinely independent.

Before moving code, inspect imports, callers, tests and ownership and record the intended responsibility boundary in the PR. This queue is therefore a work order for architectural review, not a mandate to split files mechanically by rank.

## Exemption format

No exemptions are present initially. If one becomes necessary, use an exact entry such as:

```toml
[[exemptions]]
kind = "function"
path = "src/ai_multi_agent_platform/example/generated_schema.py"
symbol = "build_generated_schema"
reason = "Generated from the canonical schema definition; hand decomposition would be lost."
```

A module exemption omits `symbol`. Exemptions for ordinary hand-written orchestration or business logic should be exceptional; a large number of exemptions is itself a maintainability signal.

## #896 completion direction

The guardrail is the first cohort, not the whole issue. #896 remains open while the highest-risk existing outliers are audited and decomposed behind stable façades. Each refactor cohort must keep the inventory useful, preserve behavior through existing/new regression tests, and leave ownership clearer than before the split.
