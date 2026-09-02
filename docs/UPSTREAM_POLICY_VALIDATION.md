# Upstream Policy Validation

These scenarios validate that `LICENSE_POLICY.md`, `docs/UPSTREAMS.md`, the provenance template, and the adoption checklist lead to unambiguous handling decisions for different integration models.

They are **policy-validation scenarios**, not declarations that the named upstream is already integrated. Candidate components become integrated only through their own implementation PR and completed registry entry.

## Scenario A — Hermes Agent as an external self-hosted service with a platform adapter

**Upstream:** `NousResearch/hermes-agent`

**Observed licensing model for this validation:** MIT-licensed upstream. The exact commit/version must still be verified and pinned by the implementation PR.

### Classification

Primary category: **External self-hosted service**.

Secondary category: **Adapter integration** for the platform-owned translation layer.

### Required handling

- Do not copy Hermes source merely to make integration convenient.
- Deploy Hermes separately and communicate through a documented interface where that provides the required capability.
- Keep canonical platform Task/Run/Agent/Event identities independent from Hermes-private identifiers.
- Record the exact deployed Hermes version/commit, verified license, review date, API boundary, deployment/resource constraints, and adapter path before promotion to `integrated`.
- Keep any model-provider requirements optional/replaceable so the platform baseline does not inherit a required recurring paid AI/API service.
- Add adapter/contract tests that detect incompatible behavior at the boundary.
- Define an exit path: another orchestrator or platform-owned implementation must be able to replace Hermes without redefining canonical contracts.

### Update handling

When a new Hermes revision is considered:

1. compare it with the pinned revision;
2. classify relevant changes;
3. inspect API/configuration/lifecycle/security/resource changes;
4. adapt only behind the platform adapter;
5. run contract/integration/regression tests;
6. update provenance metadata;
7. require an ADR if adoption would force a canonical architecture change.

### Result

The policy produces a clear answer: **use as a separately deployed, replaceable service plus a platform-owned adapter unless a later reviewed requirement justifies a more coupled mode**.

## Scenario B — `jsonschema` as a normal library dependency

**Upstream:** `python-jsonschema/jsonschema`

**Repository use at validation time:** the development dependency manifest contains `jsonschema>=4.25,<5`.

### Classification

Primary category: **Library dependency**.

### Required handling

- Keep the package outside the repository source tree; do not vendor it without a separate justification.
- The package constraint remains in dependency management.
- Verify the direct package license and review material transitive obligations if the dependency becomes a required production component.
- Isolate JSON Schema validation behind project-owned schema/validation behavior rather than exposing package-private objects as canonical platform contracts.
- When architecture-significant, record the exact resolved/pinned version, canonical upstream, license verification, compatibility constraints, and replacement path in `docs/UPSTREAMS.md`.

### Update handling

A major-version or behavior-changing update must be reviewed through a dependency PR and validated against schema/contract/regression tests. A simple development-tool update that does not affect architecture can follow normal dependency maintenance while still respecting package licensing.

### Result

The policy produces a clear answer: **normal dependency management is preferred; copying the library source would create unnecessary licensing and maintenance coupling**.

## Scenario C — selective port of upstream MIT-licensed source

This scenario tests the copied/adapted-source path. Assume a future implementation proposes copying a specific MIT-licensed helper from an upstream project instead of integrating the whole runtime.

### Classification

Primary category: **Selective code port**.

### Required handling

Before copying anything, the implementation PR must record:

- canonical upstream repository;
- exact commit and source file/function origin;
- verified license and review date;
- required copyright/license notice;
- destination path;
- whether and how the code is modified;
- why a normal dependency/service/adapter is not sufficient;
- compatibility/security constraints;
- upstream comparison/update method;
- exit/removal strategy.

The local file must retain required upstream attribution and must not be represented as wholly project-authored code. Project modifications should be marked where appropriate and the provenance record must preserve the source-to-destination mapping.

### Update handling

The maintained port must periodically check upstream changes relevant to the copied fragment, especially security and bug fixes. Relevant changes are reviewed and ported deliberately; they are never auto-merged into production.

### Result

The policy produces a clear answer: **copying is permitted only after explicit provenance/license review and only when the tighter coupling is justified; otherwise prefer the looser boundary**.

## Validation conclusion

The three scenarios exercise external-service + adapter, normal dependency, and copied/adapted-source handling. They demonstrate that contributors can determine:

- which integration category applies;
- whether source should remain external or may be copied;
- which provenance and notices are required;
- how updates are reviewed;
- when an ADR is required;
- how the upstream can later be removed or replaced.
