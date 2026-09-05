# Issue #19 completion audit — evaluation and regression framework

This document records the hardened final acceptance audit for issue #19. The original completion audit from PR #312 established the framework-level contract. PR #338 hardened the actually composed product, and the later strict post-completion audit reopened #19. PR #367 addresses the remaining exact-target, server-owned snapshot, workspace/evidence and portability gaps.

The hardened implementation is provider-neutral, orchestrator-neutral and executor-neutral. The mandatory path remains deterministic and requires no paid model or evaluator service. Distribution remains optional: EvaluationSuite assets can now use the existing #79 portability workflow, while Evaluation execution and result persistence remain independently owned.

## Hardened completion result

- **10/10 acceptance criteria: PASS**
- **10/10 required tests: PASS**
- **Declared deliverables: present**
- **Product composition: PASS**
- **Durability/restore participation: PASS**
- **Canonical authorization integration: PASS**
- **Definition of Done: PASS at implementation scope; merge remains gated by current-head Required Checks**

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
| 10 | Distribution/import/export is optional and separate from evaluation execution. | PASS | Evaluation execution has no portability dependency. When portability is composed, exact EvaluationSuite versions use the existing #79 package/preview/remapping/import pipeline and mutate only through the Evaluation-owned suite service/repository. |

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

## Product-composition hardening delivered by PR #338 and PR #367

The stricter product audits found that the framework contracts were complete but several capabilities were not yet fully connected to the shipped single-node product. PR #338 established the durable/authenticated product foundation; PR #367 closes the later exact-target, server-owned snapshot, evidence, fixture-isolation and portability gaps without adding a parallel architecture.

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

### Deployment-owned custom assets and exact product targets

Single-node Evaluation loads deployment-owned suite, regression-policy, aggregation-policy and optional model-judge configuration from `SingleNodeConfig.evaluation_dir` instead of exposing only the built-in reference suite. Directory fixtures are materialized as canonical File records into a fresh isolated Workspace for every attempt, and durable Run -> Workspace bindings make that isolation inspectable across restart.

Agent-target cases encode an exact canonical Agent revision, selected model configuration and requested capability set into Task metadata. The normal `FirstRunAgentLifecycleBackend`/`AgentRuntime`/`ModelRuntime` path executes that binding. `AgentTargetValidatingCaseExecutor` fails closed unless the recorded AgentRun proves the declared Agent revision, model/provider and capabilities actually ran.

`EvaluationTargetSnapshotEnricher` derives target configuration server-side before execution. Agent, model, provider and capability references are resolved from their owning registries, while prompt/config identity is fingerprinted from the resolved immutable Agent instructions as a SHA-256 revision. Clients therefore cannot manufacture the target snapshot by echoing arbitrary prompt/config references.

### Product evidence composition

Single-node Evaluation composes evidence from the same source owners used by the product. AgentRun evidence is always available when Agents are composed; Approval evidence reads the product `AuthorizationGate` approval service; Distributed evidence reads the supplied `DistributedRuntime`; Accounting and Observability evidence are attached only when their source-owned services/exporters are supplied. Evaluation never invents empty shadow authorities merely to satisfy assertions.

### Canonical mutable EvaluationSuite ownership and #79 portability

`EvaluationSuiteAssetRepository` is now the owning-domain mutation/persistence seam for imported exact suite versions. `SqliteEvaluationSuiteAssetRepository` stores them in the existing `evaluation.sqlite3`, while configured/built-in suites remain immutable deployment inputs. `EvaluationService` presents both sets through one suite lookup/list boundary.

The existing #79 workflow now has an `evaluation_suite` codec and mutation handler. The portability resource identity is the exact `<suite_id>@<version>` reference; Agent targets are dependency-ordered and remapped through `ImportContext`; model/capability requirements remain explicit dependencies. Apply/rollback call `EvaluationService.create_suite(...)` / `delete_suite(...)` rather than writing storage directly. Existing versions conflict during preview, imported suites survive restart, compensation is checksum-bound, and a suite version referenced by durable EvaluationRun history cannot be deleted.

Fixture references are represented as explicit `evaluation_fixture` dependencies. Because no canonical portable EvaluationFixture owner is currently composed, fixture-bearing cross-deployment imports fail closed rather than creating portability-private fixture persistence. This does not affect local product fixture execution through the deployment-owned fixture directory.

## End-to-end product verification

`tests/test_issue_19_single_node_evaluation.py` exercises the actual authenticated single-node Control Plane rather than an isolated service fixture. It verifies that Evaluation resources are discoverable, runs the built-in deterministic reference suite through `evaluation.run`, reads the produced run/results and reconstructs the deployment to prove the Evaluation history survives restart through SQLite.

Additional hardened tests cover:

- resource-limit pass/fail/missing-metric semantics;
- runtime-owned snapshot enrichment and required-component rejection before execution;
- nested immutable canonical evidence -> strict JSON projection;
- Agent/model/provider/capability/tool/model-call evidence;
- Approval evidence;
- Distributed node/worker placement evidence;
- plan/step/event projection;
- deployment-owned custom suite/policy loading and real Directory fixture isolation;
- exact Agent/model/capability product targeting with server-owned prompt/config fingerprinting;
- canonical EvaluationSuite persistence across restart;
- #79 EvaluationSuite export/preview/import with Agent remapping, conflict detection and guarded rollback.

## CI verification

PR #367 remains merge-gated by the repository's normal current-head Required Checks: Ruff format/lint, strict Mypy, full Pytest, the deterministic no-paid-service Evaluation gate, package build, single-node install smoke, frontend checks, pinned LiteLLM compatibility, CodeQL and dependency review. This audit intentionally does not freeze an older workflow-run number as final evidence while `main` is moving under parallel issue work; the PR's final GitHub checks and closure comment are authoritative for the exact merge head.

During the final product hardening, the previous #19 Agent/model E2E blocker was isolated to the canonical Task metadata deep-freeze boundary: JSON capability lists become tuples, while the decoder originally accepted only lists. The decoder now accepts both the wire JSON list and canonical frozen tuple while preserving strict element/duplicate validation, with a regression test covering encode -> canonical Task freeze -> decode. Subsequent full-suite testing advanced past that #19 E2E; unrelated failures introduced by parallel issues are not counted as #19 acceptance failures and are revalidated again after every `main` synchronization.

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
- strict-JSON-safe persistence of canonical immutable evidence;
- deployment-owned configurable suite/regression/aggregation/model-judge assets and Directory fixtures;
- exact Agent/model/capability product targets with fail-closed runtime identity validation and server-owned prompt/config fingerprints;
- Evaluation-owned durable exact-version Suite asset persistence/mutation;
- existing-#79 EvaluationSuite package/preview/remapping/import integration with guarded compensation.

## Definition of Done

**PASS.**

Significant platform, agent, model, orchestration, tool and execution changes can be measured against repeatable versioned scenarios through the canonical Evaluation runner and Control Plane/CLI surfaces. The mandatory CI profile remains deterministic and free of paid services. Product deployment can run and persist Evaluation through the authenticated single-node Control Plane, the resulting database participates in backup/restore, and source-owned behavioral evidence can be asserted without creating evaluator-private ownership of those domains.

Optional model judging, portability/distribution and more advanced statistical policies remain additive capabilities and do not block Evaluation execution. The portability-audit follow-ups recorded directly on #19 are now implemented: exact EvaluationSuite versions have an Evaluation-owned durable mutation seam and can use the existing #79 package/preview/remapping/import contracts. Fixture portability remains a future owning-domain extension and intentionally fails closed today.
