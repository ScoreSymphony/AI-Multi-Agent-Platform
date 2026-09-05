from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


portability = Path("docs/PORTABILITY.md")
insert_before = "## Dry-run and conflict boundary\n"
evaluation_section = '''## Evaluation Suite semantics

Portable `evaluation_suite` resources carry one exact canonical `EvaluationSuite` version. The portability resource ID is the existing northbound exact suite reference `<suite_id>@<version>`; the portable codec schema version is tracked separately so multiple suite versions remain independently addressable.

Imported/mutable suite ownership remains inside Evaluation. `EvaluationSuiteAssetRepository` defines the canonical create/read/delete seam and `SqliteEvaluationSuiteAssetRepository` persists imported versions in the same `evaluation.sqlite3` database used by Evaluation history. Configured/built-in suites remain immutable deployment inputs. Portability never writes that database directly: `EvaluationSuiteImportMutationHandler` applies and compensates only through `EvaluationService.create_suite(...)` and `EvaluationService.delete_suite(...)`.

Exact suite versions are create-only. Existing `<suite_id>@<version>` identities are reported as preview conflicts before mutation. Compensation is checksum-bound and refuses to delete a suite version once durable EvaluationRun history references it.

The codec declares dependencies from suite content rather than silently weakening it. Canonical Agent targets are resource dependencies and are remapped through the accepted `ImportContext`; model and capability targets remain explicit model/capability requirements. Fixture references are declared as `evaluation_fixture` resource dependencies. The current single-node composition intentionally has no portable EvaluationFixture resource/registry, so cross-deployment import of a fixture-bearing suite fails closed unless that dependency is supplied by a future owning-domain integration. Fixture bytes/paths are never smuggled into the suite payload as portability-private state.

Single-node production composition registers `evaluation_suite` on the normal #79 workflow. Evaluation execution itself still has no dependency on portability: deployments may execute configured or persisted suites without enabling export/import.

'''
replace_once(portability, insert_before, evaluation_section + insert_before)
replace_once(
    portability,
    "- guarded Automation rollback for in-memory and SQLite repositories.\n",
    "- guarded Automation rollback for in-memory and SQLite repositories;\n"
    "- EvaluationSuite codec round trip through the existing package/preview/import pipeline;\n"
    "- deterministic Agent-target remapping inside imported EvaluationSuite cases;\n"
    "- durable imported-suite restart recovery, exact-version conflict detection and checksum/history-guarded compensation.\n",
)
replace_once(
    portability,
    "#79 is complete and closed. Agent/Team, Template and Project round trips, canonical dependency/reference remapping, guarded compensation, replay-safe import reports and the Control Plane/CLI workflow form the completed portability surface. #308 is complete and Project portability consumes its canonical ScopeStore persistence seam. #309 and #310 remain independent follow-up domain work for durable model-routing and authorization-policy resources; they are not blockers to the completed #79 Definition of Done. #19 is currently reopened for its own outstanding Evaluation-framework acceptance work. Configured EvaluationSuite assets currently remain deployment/configuration inputs rather than a mutable canonical repository, so any future cross-deployment Evaluation-suite import must consume a stable owning-domain mutation seam instead of introducing portability-specific shadow persistence.\n",
    "#79 is complete and closed. Agent/Team, Template, Project and now EvaluationSuite round trips use the same package, integrity, preview, remapping and rollback-safe import contracts. #308 is complete and Project portability consumes its canonical ScopeStore persistence seam. #19 now supplies the previously missing owning-domain EvaluationSuite persistence/mutation seam and registers `evaluation_suite` through the existing #79 workflow without making Evaluation execution depend on portability. Fixture-bearing suite imports remain fail-closed until an owning-domain portable EvaluationFixture integration exists. #309 and #310 remain independent follow-up domain work for durable model-routing and authorization-policy resources; they are not blockers to the completed #79 Definition of Done.\n",
)

control = Path("docs/PORTABILITY_CONTROL_PLANE.md")
replace_once(
    control,
    "platform portability export --resource template:template_123\n",
    "platform portability export --resource template:template_123\n"
    "platform portability export --resource evaluation_suite:portable.agent-suite@1.2\n",
)
replace_once(
    control,
    "The single-node deployment composes canonical Agent, Agent Team, Project and Template export/import against their durable repositories. Template portability reuses the canonical #78 Template repository and immutable revision model rather than defining a second Template lifecycle inside #79.\n",
    "The single-node deployment composes canonical Agent, Agent Team, Project, Template and EvaluationSuite export/import against their owning-domain repositories/services. Template portability reuses the canonical #78 Template repository and immutable revision model rather than defining a second Template lifecycle inside #79.\n",
)
project_rollback = "Project rollback is deliberately fail-closed. The #308 compensation seam refuses deletion unless cross-domain dependency safety is explicitly proven and also rejects deletion when Workspace dependencies exist. A deployment that cannot provide a complete cross-domain dependency audit therefore reports incomplete compensation rather than risking deletion of a referenced Project.\n"
evaluation_control = project_rollback + "\nEvaluationSuite portability likewise reuses an owning-domain seam rather than a portability database. Imported exact suite versions are created through `EvaluationService.create_suite(...)` and persisted by `EvaluationSuiteAssetRepository` in `evaluation.sqlite3`; rollback goes through `EvaluationService.delete_suite(...)`, is bound to the imported checksum and refuses deletion after durable run history references that suite version. The suite codec remaps canonical Agent targets through the server-owned preview and declares model/capability/fixture dependencies explicitly. Fixture dependencies currently remain fail-closed in single-node until a canonical portable EvaluationFixture owner is composed.\n"
replace_once(control, project_rollback, evaluation_control)
replace_once(
    control,
    "Preview checks Model dependencies against the canonical `ModelRegistry`, Project/Workspace resource dependencies against `ScopeStore`, and existing Template dependencies against the canonical Template repository.\n",
    "Preview checks Model dependencies against the canonical `ModelRegistry`, Project/Workspace resource dependencies against `ScopeStore`, existing Template dependencies against the canonical Template repository, and exact EvaluationSuite identities against the Evaluation-owned suite service. EvaluationSuite Agent dependencies participate in the same package dependency ordering/remapping as other canonical resources.\n",
)
replace_once(
    control,
    "#79 is complete and closed. Project portability consumes the canonical #308 persistence seam. #309 and #310 remain independent follow-up domain work if durable model-routing or authorization-policy profiles are later added to the portability surface; they do not block #79's completed status. #19 is currently reopened for its own outstanding Evaluation-framework acceptance work. EvaluationSuite assets remain deployment/configuration inputs without a canonical suite-import mutation seam, so portability must not create shadow persistence to manufacture one.\n",
    "#79 is complete and closed. Project portability consumes the canonical #308 persistence seam, and #19 now provides the owning-domain EvaluationSuite mutation/persistence seam required for safe `evaluation_suite` import/export through the same workflow. Evaluation execution remains independent of portability, and fixture-bearing suites continue to fail closed until a canonical portable EvaluationFixture integration is explicitly composed. #309 and #310 remain independent follow-up domain work if durable model-routing or authorization-policy profiles are later added to the portability surface; they do not block #79's completed status.\n",
)

completion = Path("docs/ISSUE_19_COMPLETION.md")
replace_once(
    completion,
    "This document records the hardened final acceptance audit for issue #19. The original completion audit from PR #312 established the framework-level contract; PR #338 re-opened the issue and audited it against the actually composed product, then closed the remaining integration gaps.\n",
    "This document records the hardened final acceptance audit for issue #19. The original completion audit from PR #312 established the framework-level contract. PR #338 hardened the actually composed product, and the later strict post-completion audit reopened #19. PR #367 addresses the remaining exact-target, server-owned snapshot, workspace/evidence and portability gaps.\n",
)
replace_once(
    completion,
    "The hardened implementation is provider-neutral, orchestrator-neutral and executor-neutral. The mandatory path remains deterministic and requires no paid model or evaluator service. Optional portability/distribution remains a separate concern even though the platform can now compose portability and Evaluation in the same single-node product.\n",
    "The hardened implementation is provider-neutral, orchestrator-neutral and executor-neutral. The mandatory path remains deterministic and requires no paid model or evaluator service. Distribution remains optional: EvaluationSuite assets can now use the existing #79 portability workflow, while Evaluation execution and result persistence remain independently owned.\n",
)
replace_once(
    completion,
    "- **Definition of Done: PASS**\n",
    "- **Definition of Done: PASS at implementation scope; merge remains gated by current-head Required Checks**\n",
)
replace_once(
    completion,
    "| 10 | Distribution/import/export is optional and separate from evaluation execution. | PASS | Evaluation has no portability dependency. The current single-node composition can host both subsystems, but Evaluation execution, persistence, CI and APIs remain independently owned. |\n",
    "| 10 | Distribution/import/export is optional and separate from evaluation execution. | PASS | Evaluation execution has no portability dependency. When portability is composed, exact EvaluationSuite versions use the existing #79 package/preview/remapping/import pipeline and mutate only through the Evaluation-owned suite service/repository. |\n",
)
replace_once(
    completion,
    "## Product-composition hardening delivered by PR #338\n\nThe stricter product audit found that the framework contracts were complete but several capabilities were not yet fully connected to the shipped single-node product. PR #338 resolves those gaps without adding a parallel architecture.\n",
    "## Product-composition hardening delivered by PR #338 and PR #367\n\nThe stricter product audits found that the framework contracts were complete but several capabilities were not yet fully connected to the shipped single-node product. PR #338 established the durable/authenticated product foundation; PR #367 closes the later exact-target, server-owned snapshot, evidence, fixture-isolation and portability gaps without adding a parallel architecture.\n",
)
end_to_end = "## End-to-end product verification\n"
new_sections = '''### Deployment-owned custom assets and exact product targets

Single-node Evaluation loads deployment-owned suite, regression-policy, aggregation-policy and optional model-judge configuration from `SingleNodeConfig.evaluation_dir` instead of exposing only the built-in reference suite. Directory fixtures are materialized as canonical File records into a fresh isolated Workspace for every attempt, and durable Run -> Workspace bindings make that isolation inspectable across restart.

Agent-target cases encode an exact canonical Agent revision, selected model configuration and requested capability set into Task metadata. The normal `FirstRunAgentLifecycleBackend`/`AgentRuntime`/`ModelRuntime` path executes that binding. `AgentTargetValidatingCaseExecutor` fails closed unless the recorded AgentRun proves the declared Agent revision, model/provider and capabilities actually ran.

`EvaluationTargetSnapshotEnricher` derives target configuration server-side before execution. Agent, model, provider and capability references are resolved from their owning registries, while prompt/config identity is fingerprinted from the resolved immutable Agent instructions as a SHA-256 revision. Clients therefore cannot manufacture the target snapshot by echoing arbitrary prompt/config references.

### Product evidence composition

Single-node Evaluation composes evidence from the same source owners used by the product. AgentRun evidence is always available when Agents are composed; Approval evidence reads the product `AuthorizationGate` approval service; Distributed evidence reads the supplied `DistributedRuntime`; Accounting and Observability evidence are attached only when their source-owned services/exporters are supplied. Evaluation never invents empty shadow authorities merely to satisfy assertions.

### Canonical mutable EvaluationSuite ownership and #79 portability

`EvaluationSuiteAssetRepository` is now the owning-domain mutation/persistence seam for imported exact suite versions. `SqliteEvaluationSuiteAssetRepository` stores them in the existing `evaluation.sqlite3`, while configured/built-in suites remain immutable deployment inputs. `EvaluationService` presents both sets through one suite lookup/list boundary.

The existing #79 workflow now has an `evaluation_suite` codec and mutation handler. The portability resource identity is the exact `<suite_id>@<version>` reference; Agent targets are dependency-ordered and remapped through `ImportContext`; model/capability requirements remain explicit dependencies. Apply/rollback call `EvaluationService.create_suite(...)` / `delete_suite(...)` rather than writing storage directly. Existing versions conflict during preview, imported suites survive restart, compensation is checksum-bound, and a suite version referenced by durable EvaluationRun history cannot be deleted.

Fixture references are represented as explicit `evaluation_fixture` dependencies. Because no canonical portable EvaluationFixture owner is currently composed, fixture-bearing cross-deployment imports fail closed rather than creating portability-private fixture persistence. This does not affect local product fixture execution through the deployment-owned fixture directory.

'''
replace_once(completion, end_to_end, new_sections + end_to_end)
replace_once(
    completion,
    "- plan/step/event projection.\n",
    "- plan/step/event projection;\n"
    "- deployment-owned custom suite/policy loading and real Directory fixture isolation;\n"
    "- exact Agent/model/capability product targeting with server-owned prompt/config fingerprinting;\n"
    "- canonical EvaluationSuite persistence across restart;\n"
    "- #79 EvaluationSuite export/preview/import with Agent remapping, conflict detection and guarded rollback.\n",
)
ci_start = completion.read_text(encoding="utf-8").index("## CI verification\n")
ci_end = completion.read_text(encoding="utf-8").index("## Deliverables audit\n", ci_start)
text = completion.read_text(encoding="utf-8")
ci_section = '''## CI verification

PR #367 remains merge-gated by the repository's normal current-head Required Checks: Ruff format/lint, strict Mypy, full Pytest, the deterministic no-paid-service Evaluation gate, package build, single-node install smoke, frontend checks, pinned LiteLLM compatibility, CodeQL and dependency review. This audit intentionally does not freeze an older workflow-run number as final evidence while `main` is moving under parallel issue work; the PR's final GitHub checks and closure comment are authoritative for the exact merge head.

During the final product hardening, the previous #19 Agent/model E2E blocker was isolated to the canonical Task metadata deep-freeze boundary: JSON capability lists become tuples, while the decoder originally accepted only lists. The decoder now accepts both the wire JSON list and canonical frozen tuple while preserving strict element/duplicate validation, with a regression test covering encode -> canonical Task freeze -> decode. Subsequent full-suite testing advanced past that #19 E2E; unrelated failures introduced by parallel issues are not counted as #19 acceptance failures and are revalidated again after every `main` synchronization.

'''
completion.write_text(text[:ci_start] + ci_section + text[ci_end:], encoding="utf-8")
replace_once(
    completion,
    "- strict-JSON-safe persistence of canonical immutable evidence.\n",
    "- strict-JSON-safe persistence of canonical immutable evidence;\n"
    "- deployment-owned configurable suite/regression/aggregation/model-judge assets and Directory fixtures;\n"
    "- exact Agent/model/capability product targets with fail-closed runtime identity validation and server-owned prompt/config fingerprints;\n"
    "- Evaluation-owned durable exact-version Suite asset persistence/mutation;\n"
    "- existing-#79 EvaluationSuite package/preview/remapping/import integration with guarded compensation.\n",
)
replace_once(
    completion,
    "Optional model judging, portability/distribution and more advanced statistical policies remain additive capabilities and do not block the #19 framework.\n",
    "Optional model judging, portability/distribution and more advanced statistical policies remain additive capabilities and do not block Evaluation execution. The portability-audit follow-ups recorded directly on #19 are now implemented: exact EvaluationSuite versions have an Evaluation-owned durable mutation seam and can use the existing #79 package/preview/remapping/import contracts. Fixture portability remains a future owning-domain extension and intentionally fails closed today.\n",
)
