# Canonical data classification and egress policy

Issue #591 defines one platform-owned policy boundary for outbound data movement. The boundary is
provider-neutral: model providers, connectors, MCP servers, browser/network capabilities, remote
workers and export adapters remain replaceable, while classification and disclosure policy stay in
the application layer.

## Canonical classification

`DataClassification` is the shared sensitivity vocabulary. The canonical levels are `public`,
`internal`, `confidential`, `secret` and `regulated`. `private`, `restricted` and
`secret_reference` remain supported because they are already persisted by older platform domains.
The ordering is monotonic: derived data must retain the strongest source classification. A lower
classification is only legal for an explicitly policy-authorized redacted/minimized derivative;
relabelling content is not a downgrade mechanism.

Context Bundles merge entry classifications before export. File export merges the canonical file
classification with the current `DataAccessContext`. Connector-derived results/resources/events
inherit at least the originating classification and preserve a stronger provider-reported class.

## Egress targets and profiles

`EgressTarget` references the canonical identity already owned by the destination domain. It never
creates a second provider registry. A model target therefore uses the existing
`ModelConfiguration.config_id`; connector/capability/runtime targets follow the same rule.

`EgressProfile` (`egress-profile/v1`) is an optional, versioned policy view attached to that target.
It records:

- target posture (`local`, `internal`, `external`, `unknown`);
- explicitly allowed and denied data classifications;
- whether network egress is required;
- retention, training, logging and jurisdiction assertions when known;
- cost class (`local`, `included`, `free_external`, `paid_external`, `unknown`);
- whether credentials are required;
- policy source and source revision;
- trust (`configured`, `verified`, `unverified`).

Profile assertions are not silently trusted. An external `unverified` profile is blocked. Unknown
posture is blocked. For profile-aware external destinations, unknown cost is blocked and
`paid_external` is denied by the baseline policy. A deployment may explicitly instantiate
`CanonicalEgressPolicy(allow_paid_external=True)` when it deliberately permits paid external
routes. This is a policy decision, not a router heuristic.

Legacy targets without a profile retain the original v1 posture/classification behavior so existing
configurations do not change meaning merely because the profile contract was introduced. New
external integrations should define a versioned profile rather than rely on that compatibility
path.

## Model routing

The default `ModelRuntime` installs an egress-aware candidate hook into
`DeterministicModelRouter`. Candidates are first filtered for ordinary model capabilities and then
for disclosure policy. A prohibited high-priority remote model is excluded before selection, so an
allowed lower-priority local/self-hosted model can be chosen safely.

An explicit model assignment is different: if that exact destination is prohibited, routing fails
with `NO_COMPATIBLE_ROUTE`; it is not silently replaced by a broader or different disclosure path.
The selection metadata records safe structured `policy_exclusions` with canonical model/provider
IDs and reason codes. `ModelRuntime` enforces the same egress request again immediately before the
provider call as defense in depth and for custom routers.

Prompt/model text is never classification authority. The model path reads the platform-owned
`data_classification` routing metadata. A prompt cannot downgrade itself by saying that its content
is public.

## Capabilities, MCP, connectors and browser networking

The public `CapabilityInvoker` is the egress-aware invoker. `EXTERNAL` capabilities and distributed
capabilities cross the gate before provider execution. MCP tool registrations already declare
external side effects and therefore use the same gate.

Transport classification is intentionally distinct from mutation side effects. Browser navigation,
link following and downloads can be read-only or local-write operations while still sending data
over HTTP(S). `browser.network.read` and the generic `network.egress*` permission family therefore
mark the transport as external even when `side_effects` is not `EXTERNAL`. This prevents read-only
browser calls from bypassing #591.

`EgressConnectorService` checks list/read/action/subscription/sync outbound requests before the
connector provider is called. Returned canonical resources, events and action results inherit the
strongest originating classification so a provider cannot weaken derived data.

## Context, files and artifacts

`ContextBundleEgressExporter` evaluates the effective Context Bundle classification before rendering
or releasing the bundle to its destination. `FileEgressExporter` evaluates file/artifact export
using the strongest canonical file/access-context classification before reading the outbound bytes,
and verifies the SHA-256 after the policy decision so the exported content cannot change between
evaluation and transfer.

Secrets remain references governed by the existing secret subsystem. Egress policy does not move
secret material into provider metadata, audit events or configuration values.

## Decisions and audit evidence

`EgressGate` is the single enforcement seam. The policy returns an `EgressDecision`; only
`ALLOW` is executable. `DENY`, `REQUIRE_APPROVAL`, `LOCAL_ONLY` and `UNKNOWN_BLOCKED` are all
non-executable until a higher-level governance flow resolves them into a new valid decision.

The gate emits value-free `EgressEvaluated` followed by `EgressAllowed` or `EgressDenied`. Audit
records can contain canonical target identity, effective classification, payload digest, policy
version, profile revision, cost class, correlation/task/run/capability references and safe policy
metadata. Raw prompt text, connector arguments, file bytes, secret material and protected payload
values must never be copied into egress audit events.

## Current approval boundary

The egress contracts reserve `REQUIRE_APPROVAL` and `approval_ref`, but the baseline policy does not
convert a denial into approval on its own. Exact-action approval lifecycle and expiry are owned by
the existing Authorization/Approval subsystem. Until a dedicated #591 approval bridge binds an
approved action digest to the exact egress request/profile revision, an approval-required egress
decision remains fail-closed.
