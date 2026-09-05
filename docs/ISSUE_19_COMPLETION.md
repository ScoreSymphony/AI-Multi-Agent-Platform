# Issue #19 completion audit — evaluation and regression framework

This document records the hardened final acceptance audit for issue #19. The original completion audit from PR #312 established the framework-level contract; PR #338 re-opened the issue and audited it against the actually composed product, then closed the remaining integration gaps.

The hardened implementation is provider-neutral, orchestrator-neutral and executor-neutral. The mandatory path remains deterministic and requires no paid model or evaluator service. Optional portability/distribution remains a separate concern even though the platform can now compose portability and Evaluation in the same single-node product.

## Hardened completion result

- **10/10 acceptance criteria: PASS**
- **10/10 required tests: PASS**
- **Declared deliverables: present**
- **Product composition: PASS**
- **Durability/restore participation: PASS**
- **Canonical authorization integration: PASS**
- **Definition of Done: PASS**

## Acceptance criteria

| # | Criterion | Result | Hardened evidence |
|---|---|---|---|
| 1 | Canonical evaluation case/suite/run/result models exist. | PASS | `evaluation/models.py` owns `EvaluationCase`, `EvaluationSuite`, `EvaluationRun`, `EvaluationResult`, attempts, snapshots, aggregation and regression/comparison contracts. |
| 2 | Runs record relevant platform, agent, model, tool, orchestrator and executor versions/configuration. | PASS | `ConfigurationSnapshot` is immutable and versioned. `EvaluationRunner` now enriches snapshots with runtime-owned suite/evaluator identities and validates required component kinds before execution; richer compatible revisions are preserved rather than overwritten. First-party Agent evidence projects agent revision, model/provider selection, capabilities, model calls and tool invocations. |
| 3 | Deterministic evaluations work without an LLM evaluator. | PASS | `DeterministicAssertionEvaluator`, `MetricThresholdEvaluator` and the new deterministic `ResourceLimitEvaluator` require no model endpoint. The checked-in CI gate remains no-paid-service. |
| 4 | Reference scenarios can be repeated and compared to a baseline. | PASS | `EvaluationRunner`, durable SQLite history, explicit repetitions/seeds, aggregation policies and `RegressionEngine` cover repeatable runs and accepted-baseline comparison. |
| 5 | Regression rules/thresholds are versioned. | PASS | `RegressionPolicy`, case `MetricRule`, rubric thresholds and aggregation policies are explicit versioned data rather than evaluator implementation constants. |
| 6 | CI has a deterministic no-paid-service evaluation subset. | PASS | `scripts/ci/issue19_evaluation_gate.py` executes the checked-in deterministic suite/policy/baseline through the canonical reference Task/Run path after Pytest and before package build. |
| 7 | Results link to underlying tasks/runs/artifacts/telemetry. | PASS | `EvaluationObservation`/`EvaluationResult` retain canonical Task/Run/Artifact/telemetry evidence. First-party adapters additionally project plan/steps/events, Agent/model/provider/capability/model-call/tool-call evidence, Approval state and Distributed node/worker placement. Source-owned IDs are retained; evidence is not fabricated. |
| 8 | Evaluators are replaceable. | PASS | Sync/async `EvaluatorLike` boundaries, safe evaluator containment and the deterministic/metric/rubric/model-judge implementations all emit the same canonical result contract. |
| 9 | Optional model judging records evaluator identity/configuration explicitly. | PASS | `ModelJudgeEvaluator` uses canonical `ModelRuntime`; evaluator/model/provider/configuration identities are persisted explicitly and the evaluator is marked non-deterministic. |
| 10 | Distribution/import/export is optional and separate from evaluation execution. | PASS | Evaluation has no portability dependency. The current single-node composition can host both subsystems, but Evaluation execution, persistence, CI and APIs remain independently owned. |

## Required tests

| Required test | Result | Concrete coverage |
|---|---|---|
| deterministic pass/fail | PASS | `tests/test_evaluation_foundation.py` plus hardened `behavior.*` assertion coverage |
| baseline comparison | PASS | foundation regression tests and restart-safe SQLite history tests |
| threshold regression | PASS | score-drop, metric-threshold and checked-in CI regression coverage |
| critical-case regression | PASS | critical/security tag policy tests and deliberate CI regression coverage |
| configuration snapshot integrity | PASS | duplicate identity checks plus hardened required-kind/runtime-enrichment tests |
| isolated workspace repetition | PASS | `tests/test_evaluation_workspace_isolation.py` |
| model/provider version difference | PASS | foundation snapshot/model/provider tests plus Agent evidence projection |
| evaluator failure handling | PASS | safe sync/async evaluator error containment tests |
| optional model-evaluator metadata | PASS | model-judge descriptor/runtime tests |
| local/reference CI suite | PASS | `tests/test_evaluation_ci_gate.py` and the mandatory deterministic CI gate |

## Product-composition hardening delivered by PR #338

The stricter product audit found that the framework contracts were complete but several capabilities were not yet fully connected to the shipped single-node product. PR #338 resolves those gaps without adding a parallel architecture.

### Durable single-node Evaluation

`build_single_node_deployment(...)` now composes a real Evaluation service backed by `db/evaluation.sqlite3`, registers Evaluation resources and commands on the normal authenticated Control Plane, and exposes the repository/service on `SingleNodeDeployment`.

The composition preserves the current portability workflow and agent routing-profile wiring. Evaluation therefore coexists with the platform's other current single-node subsystems rather than replacing or bypassing them.

`db/evaluation.sqlite3` is also part of `SINGLE_NODE_DURABLE_STORES`, so the Evaluation database participates in the authoritative backup/restore inventory instead of becoming an untracked side database.

### Canonical authorization integration

Evaluation routes use the normal Control Plane authorization path. The canonical `AuthorizationAction` vocabulary now includes:

- `evaluation-suite:list`;
- `evaluation-suite:read`;
- `evaluation-run:list`;
- `evaluation-run:read`;
- `evaluation.run`;
- `evaluation.compare`.

This removes the previous product-level mismatch where Evaluation endpoints existed but local authorization correctly rejected their unknown actions.

### Executable resource limits

`EvaluationCase.resource_limits` now has deterministic semantics through `ResourceLimitEvaluator`: each configured entry is a maximum for the named observed metric (`observed <= configured limit`). Missing required metrics fail the resource-limit result; invalid/non-finite/negative limits are rejected. These are evaluation acceptance limits over canonical observed metrics, not an executor-private sandbox contract.

### Assertable non-output behavior

`observation_assertion_payload(...)` exposes platform-owned observation metadata under reserved `behavior.*`, including Task/Run identity, artifacts, telemetry, selected model/provider, capabilities and event types. This makes non-output behavior deterministic-assertable without duplicating source-owned runtime state into evaluator-private models.

First-party evidence adapters cover the remaining platform behavior called out by the issue: Agent/model/capability activity, plan/step/event evidence, Approval state and Distributed node/worker placement.

### Strict JSON persistence boundary

Canonical domain events intentionally deep-freeze nested mappings/sequences. Evaluation's strict JSON persistence boundary now recursively projects those immutable containers back to plain JSON-compatible dict/list/scalar values before assertion evidence is persisted. Unsupported values and non-finite floats fail explicitly; nothing is silently stringified. Domain immutability remains unchanged.

### Snapshot completeness

The runner adds runtime-owned exact suite/evaluator/component references to each `ConfigurationSnapshot` and can require deployment-relevant component kinds before executing a suite. Compatible caller-provided references with a more precise revision are retained; conflicting versions/revisions are rejected.

## End-to-end product verification

`tests/test_issue_19_single_node_evaluation.py` exercises the actual authenticated single-node Control Plane rather than an isolated service fixture. It verifies that Evaluation resources are discoverable, runs the built-in deterministic reference suite through `evaluation.run`, reads the produced run/results and reconstructs the deployment to prove the Evaluation history survives restart through SQLite.

Additional hardened tests cover:

- resource-limit pass/fail/missing-metric semantics;
- runtime-owned snapshot enrichment and required-component rejection before execution;
- nested immutable canonical evidence -> strict JSON projection;
- Agent/model/provider/capability/tool/model-call evidence;
- Approval evidence;
- Distributed node/worker placement evidence;
- plan/step/event projection.

## CI verification

The hardened implementation was tested by GitHub Actions against the then-current PR merge result containing `main@7ca49de6c6cce4ed80d52218505757b3c017735e` before the final documentation-only update. Workflow run `33980564693` passed the main `test` job with:

- Ruff format: PASS;
- Ruff lint: PASS;
- strict Mypy: PASS;
- full Pytest: **1374 passed, 3 skipped**;
- deterministic Evaluation gate: PASS with **0 regressions**;
- package build: PASS.

The same run also passed the single-node install smoke, frontend and pinned LiteLLM compatibility checks; downstream required compatibility/security checks remain governed by normal branch protection. The branch was then explicitly merged with `main@7ca49de6c6cce4ed80d52218505757b3c017735e` so the final PR history contains the tested Portability/Security state rather than relying only on GitHub's ephemeral merge ref.

## Deliverables audit

All declared deliverables are present:

- canonical EvaluationCase/Suite/Run/Result models;
- replaceable sync/async evaluator boundary;
- deterministic assertion, metric threshold and resource-limit evaluators;
- rubric scorer and optional model-judge boundary;
- immutable/versioned configuration snapshots with runtime-owned completeness checks;
- in-memory and restart-safe SQLite persistence/history;
- baseline comparison and versioned regression engine;
- explicit stochastic aggregation for repeated samples;
- checked-in deterministic no-paid PR suite/policy/baseline;
- authenticated Control Plane Evaluation resources/commands;
- API-first `platform eval` CLI surface;
- durable single-node product composition and backup/restore inventory;
- canonical authorization vocabulary;
- Task/Run/Artifact/Workspace/telemetry/accounting provenance;
- Agent/model/provider/capability/tool/model-call, Approval, plan/event and Distributed placement evidence;
- strict-JSON-safe persistence of canonical immutable evidence.

## Definition of Done

**PASS.**

Significant platform, agent, model, orchestration, tool and execution changes can be measured against repeatable versioned scenarios through the canonical Evaluation runner and Control Plane/CLI surfaces. The mandatory CI profile remains deterministic and free of paid services. Product deployment can run and persist Evaluation through the authenticated single-node Control Plane, the resulting database participates in backup/restore, and source-owned behavioral evidence can be asserted without creating evaluator-private ownership of those domains.

Optional model judging, portability/distribution and more advanced statistical policies remain additive capabilities and do not block the #19 framework.
