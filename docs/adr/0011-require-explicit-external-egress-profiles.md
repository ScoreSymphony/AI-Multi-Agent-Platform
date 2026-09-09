# ADR 0011: Require explicit external egress profiles in production-shaped durable runtimes

## Status

Accepted

## Context

Issue #591 introduced the platform-owned data-classification and egress-policy boundary. The first
implementation deliberately retained a compatibility path for external targets that had no
`EgressProfile`: the target posture and data classification could still be evaluated, but trust and
cost attributes were absent.

That compatibility behavior is insufficient for the normal production-shaped durable runtime. A
profileless external target has no canonical cost class or trust provenance, so the platform cannot
prove the baseline constraints that paid external routes are denied and unknown external cost/trust
fails closed. Treating the absence of those attributes as equivalent to a known free/configured
external route would make the security result depend on missing metadata rather than an explicit
policy decision.

The platform must preserve provider neutrality and must not create a second provider registry merely
to solve this problem. Existing model, capability, Connector and other destination owners remain
authoritative for target identity. The egress layer owns only disclosure eligibility and its
versioned policy profile.

## Decision

The production-shaped durable egress runtime requires an explicit `EgressProfile` for every target
whose effective posture is `external`.

The canonical resolution order is:

1. resolve an enabled exact-Project durable profile when one exists;
2. otherwise resolve an enabled global durable profile for the same canonical target;
3. otherwise accept an inline profile already supplied by the canonical owning domain;
4. if the target is still external and has no profile, fail closed with `UNKNOWN_BLOCKED` and
   `UNVERIFIED_PROFILE` before provider execution.

An explicit profile is necessary but not sufficient for disclosure. Existing classification, trust,
cost, sensitive-data and Approval checks still apply. In particular, an external `unverified`
profile remains blocked, unknown external cost remains blocked by the baseline, and `paid_external`
remains denied unless a deployment explicitly changes that policy.

`build_durable_egress_runtime()` is the production-shaped composition boundary and therefore enables
this rule by default. The public single-node/server deployment inherits that default and shares the
same durable gate across model routing/invocation, capabilities/MCP/browser networking, Connectors,
Context rendering, Handoffs and file/artifact egress.

Direct lower-level `CanonicalEgressPolicy` use retains the original profileless compatibility
semantics because it is a policy primitive rather than the normative production composition.
Focused embeddings that intentionally need the old durable behavior may opt out explicitly with
`require_external_profile=False`. Such an opt-out is not the platform production baseline and must
not be used to claim the no-paid/unknown-external guarantee.

## Consequences

- The production baseline can distinguish a configured external route from an external target whose
  trust/cost policy is unknown.
- Missing profile metadata can no longer bypass `allow_paid_external=False` or
  `allow_unknown_external_cost=False` in the durable production path.
- Local and internal profileless targets preserve their established behavior.
- Provider/model/capability/Connector identity ownership remains unchanged; no second provider
  registry is introduced.
- Deployments adding a new external integration must supply an explicit durable or canonical inline
  profile before that route becomes eligible.
- Existing external configurations that depended on the profileless legacy path must add an
  `EgressProfile` or explicitly choose the non-production compatibility opt-out.
- Denial evidence remains value-free; protected outbound payloads are not added to audit metadata.

## Alternatives considered

### Keep profileless external targets allowed

Rejected for the production baseline. It preserves maximum compatibility but makes trust and cost
policy unverifiable when the relevant metadata is absent.

### Treat every profileless external target as paid

Rejected. This would fail closed but would invent a cost fact the platform does not know. The
canonical result is unknown/unverified, not paid.

### Add trust/cost fields directly to every provider registry

Rejected. That would duplicate the cross-cutting policy contract across domain owners and risk a
second provider/policy authority. `EgressProfile` remains the provider-neutral policy view.

### Remove all profileless compatibility behavior everywhere

Rejected. Lower-level policy primitives and focused embeddings may need compatibility semantics.
The strict rule belongs to the normative durable production composition, where the security
baseline is claimed.

## Affected issues/contracts

- #591 — canonical data classification and provider egress policy enforcement
- #10 — model registry/router integration
- #12 — capability/tool provider integration
- #15 — authorization/Approval remains cumulative and authoritative for access decisions
- #44 — Connector provider integration
- #74 — browser/network capability integration
- #590 — Context Bundle egress integration
- `EgressProfile`
- `RepositoryBackedEgressPolicy`
- `build_durable_egress_runtime()`
- public single-node/server composition
