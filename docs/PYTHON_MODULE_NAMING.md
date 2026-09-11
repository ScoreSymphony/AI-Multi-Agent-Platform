# Python module naming and source discoverability

This document is the contributor and coding-agent policy for Python module names below
`src/ai_multi_agent_platform/`. It complements the top-level ownership rules in
[`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md): #726 decides *which package owns a responsibility*;
this policy decides *how modules inside that package describe the responsibility*. It also extends
#721's rule that production structure is responsibility-shaped rather than issue-history-shaped.

## Rule

Choose the narrowest stable filename that communicates the module's real responsibility in an IDE
tab, traceback or code-search result. Package context still matters, so generic conventional names
are not forbidden. They are acceptable only while they remain genuinely cohesive and unsurprising.

- `service.py` is acceptable in a small package with one obvious service boundary. In a large package,
  or when the module represents a substantial named workflow, coordinator, authority, registry or
  façade, use that responsibility in the filename.
- `models.py` is acceptable for a cohesive set of declarative domain/value/transport models owned by
  one package. Split it when unrelated model families or substantial behavior accumulate; do not
  rename a mixed model bucket to a misleadingly narrow name.
- `control_plane.py` is acceptable for a thin package-local adapter that exposes the package's
  canonical owner through the shared Control Plane. If it owns several independent adapter families
  or begins to contain domain behavior, split by exposed responsibility before choosing semantic
  adapter filenames.
- `service.py`, `models.py`, `control_plane.py`, `utils.py`, `helpers.py` and similar names must not be
  used as catch-all destinations merely because code needs somewhere to live.
- Production filenames must describe durable responsibilities, never GitHub issue numbers. The #721
  issue-number guard remains authoritative.
- A semantic rename must not disguise a monolith. Extract mixed responsibilities first, following the
  #723 decomposition rule, then name the resulting implementation after what it actually owns.
- A supported historical module path may remain as a compatibility shim. Such a shim contains
  re-exports only, has no implementation classes/functions, and is not a canonical import target for
  new production code.

## Audit snapshot for #727

The repository-wide GitHub code-search baseline on 2026-09-11 at `main` commit
`bef35be8f859990620daafd5e723b90b70065a49` contained 47 files named `service.py`, 46 named
`models.py`, and 57 named `control_plane.py`. The counts are an ambiguity signal, not a rename quota.
The audit therefore ranks concrete responsibility and implementation size above basename frequency.

| Rank | Existing module | Audit result | Canonical responsibility-specific module |
| --- | --- | --- | --- |
| High | `planning/service.py` | Post-#723 stable planning façade over extracted responsibilities. | `planning/planning_facade.py` |
| High | `coordination/service.py` | Durable canonical Plan/Step runtime coordinator. | `coordination/plan_step_coordinator.py` |
| High | `learning/service.py` | Cohesive governed feedback/candidate/promotion workflow after surrounding repository, promotion, governance and runtime responsibilities were already separated. | `learning/governed_learning_workflow.py` |
| High | `onboarding/service.py` | First-run local/self-hosted model golden-path orchestration. | `onboarding/first_run_service.py` |
| High | `verification/service.py` | Canonical verification authority and completion-policy evaluator. | `verification/verification_authority.py` |
| Medium | package-local `control_plane.py` modules | Keep when they are thin adapters over an existing owner; split if domain logic or several unrelated adapter families accumulate. | Context-dependent |
| Low/medium | package-local `models.py` modules | Keep when declarative and domain-cohesive; split only at real model-family boundaries. | Context-dependent |
| Context-dependent | remaining `service.py` modules | Review when touched; keep small single-service packages, rename substantial named responsibilities, and split mixed modules before renaming. | Context-dependent |

The five high-value paths above intentionally retain import-only `service.py` shims. Their
implementation objects are defined in the semantic modules, so tracebacks and `__module__` metadata
identify the responsibility-specific filenames while supported old imports keep working.

## Review checklist

For every new or materially expanded Python module, reviewers and coding agents should ask:

1. Can the responsibility be stated without the words "service", "models", "helper" or "control
   plane"? If yes, prefer that durable responsibility when the generic basename would be ambiguous.
2. Does the proposed name still describe all implementation in the file? If not, split first.
3. Is a generic name conventional and genuinely singular inside this package? If yes, keeping it is
   preferable to cosmetic churn.
4. Will the filename be understandable in a traceback or editor tab without relying on Git history?
5. Does the module stay inside the canonical owner from `PACKAGE_BOUNDARIES.toml` and avoid creating a
   competing lifecycle authority?
6. If a historical import path is supported, is it a bounded re-export-only compatibility shim and
   are new package exports wired to the semantic implementation?

Architecture tests cover the renamed high-value modules and their compatibility shims. Broader generic
names remain a review-time rule by design so the repository does not turn a nuanced naming policy into
a brittle blanket filename ban.
