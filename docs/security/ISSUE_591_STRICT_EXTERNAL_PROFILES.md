# Issue #591 strict external-profile follow-up

## Purpose

The original #591 implementation retained a compatibility path for known external targets that did
not expose an `EgressProfile`. That path preserved historical behavior, but it also meant the
production-shaped durable runtime could not prove the baseline "no paid/unknown external route"
policy when cost and trust metadata were absent entirely.

This follow-up closes that gap without removing the lower-level compatibility seam. The material
security-boundary decision is normative in
[ADR 0011](../adr/0011-require-explicit-external-egress-profiles.md) and is reflected in
`docs/ARCHITECTURE_PRINCIPLES.md` and `docs/security/DATA_CLASSIFICATION_AND_EGRESS.md`.

## Production rule

`build_durable_egress_runtime()` now requires an explicit profile for external targets by default.
The rule applies uniformly to the shared durable gate used by model, capability/MCP/browser,
Connector, Context and file/artifact egress composition.

Resolution remains provider-neutral:

1. resolve an exact Project-scoped or global durable profile when one exists;
2. otherwise honor an inline profile supplied by the canonical owning domain;
3. if the target is external and still has no profile, return `UNKNOWN_BLOCKED` with
   `UNVERIFIED_PROFILE` before provider execution;
4. local and internal profileless targets retain their established behavior.

An external profile must still pass the existing trust, classification and cost checks. Requiring a
profile therefore does not itself grant disclosure authority.

## Compatibility

Direct `CanonicalEgressPolicy` use keeps the historical profileless behavior. Focused embeddings
that intentionally need the old durable behavior can construct the runtime with:

```python
build_durable_egress_runtime(path, require_external_profile=False)
```

The normal public single-node/server composition does not opt out and therefore inherits strict
external-profile enforcement.

## Security effect

The durable baseline can now technically distinguish an explicitly classified external route from
an external destination whose trust/cost posture is incomplete. A missing profile cannot bypass
`allow_paid_external=False` / `allow_unknown_external_cost=False` merely because no cost metadata
exists to inspect.

The denial remains value-free and auditable. No protected payload is added to policy metadata or
telemetry.

## Regression coverage

`tests/integration/security/test_strict_external_profiles.py` proves:

- profileless external targets are `UNKNOWN_BLOCKED` by the durable runtime default;
- the public single-node composition inherits the strict rule;
- profileless local targets remain functional;
- an explicit configured/free external profile satisfies the profile-presence requirement and is
  still evaluated by the canonical policy;
- focused compatibility embeddings can opt out explicitly.
