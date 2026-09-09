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

`EgressProfile` (`egress-profile/v1`) is a versioned policy view for that target. It records:

- target posture (`local`, `internal`, `external`, `unknown`);
- explicitly allowed and denied data classifications;
- whether network egress is required;
- retention, training, logging and jurisdiction assertions when known;
- cost class (`local`, `included`, `free_external`, `paid_external`, `unknown`);
- whether credentials are required;
- policy source and source revision;
- trust (`configured`, `verified`, `unverified`).

Profile assertions are not silently trusted. An external `unverified` profile is blocked. Unknown
posture is blocked. For external destinations, unknown cost is blocked and `paid_external` is denied
by the baseline policy. A deployment may explicitly instantiate
`CanonicalEgressPolicy(allow_paid_external=True)` when it deliberately permits paid external
routes. This is a policy decision, not a router heuristic.

ADR 0011 makes explicit profiles part of the production-shaped durable security boundary. A target
whose effective posture is `external` must resolve an enabled Project/global durable profile or
carry an inline profile from its canonical owning domain. If neither exists, the durable runtime
fails closed with `UNKNOWN_BLOCKED` / `UNVERIFIED_PROFILE` before provider execution. Profileless
`local` and `internal` targets keep their established behavior. Direct lower-level
`CanonicalEgressPolicy` use retains the original compatibility semantics, and focused embeddings may
explicitly opt out of the durable strict rule; that opt-out is not the normative production
baseline and cannot claim the no-paid/unknown-external guarantee.

## Durable profile history and scope

`JsonEgressProfileRepository` is the dependency-free reference persistence implementation. It stores
a stable `EgressProfileDefinition` plus complete contiguous immutable revision history and writes
atomically. Provider/model/capability/connector identity remains outside this store.

A target may have one global deployment profile and one profile per Project. Resolution is
deterministic: an enabled profile for the exact Project wins; otherwise the enabled global profile
is used. Duplicate profiles for the same target/scope are rejected rather than resolved by order.
Disabling a Project-specific profile exposes the applicable global default rather than inventing a
new policy.

`EgressProfileService` owns the authorized lifecycle. Ordinary create/version operations may produce
`configured` or `unverified` policy assertions, but they may not self-assert `verified`. Verification
is a separate revision-producing transition with an explicit verification reference and authorized
principal. Exact historical refs use `profile_id@revision` and are retained for audit and stale
Approval detection.

## Runtime composition

`RepositoryBackedEgressPolicy` resolves the current durable profile from the canonical target ID and
Project context immediately at the policy boundary, then delegates to `CanonicalEgressPolicy`.
This keeps policy resolution independent of the provider registries and means the same resolver can
be shared by model, capability, Connector, Context Bundle and file/artifact egress gates.

`build_durable_egress_runtime()` composes the repository, authorized profile service and one reusable
`EgressGate`. It defaults to no paid-external access, no optimistic unknown-cost access, no Approval
exceptions and, per ADR 0011, explicit profiles for external targets. Deployments pass that same
gate to provider-facing runtimes so policy configuration and execution observe the same revision
history. A focused compatibility embedding may set `require_external_profile=False`, but the public
single-node/server composition does not opt out.

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

## Approval exceptions

Optional exceptions reuse the existing #15 `AuthorizationGate`/`ApprovalService`; #591 does not add
a second approval engine. `EgressApprovalExceptionPolicy` is deny-by-default and requires a
deployment to enumerate the exact egress reason codes and data classes that may be approved.
`secret` and `secret_reference` are non-overridable by default.

`EgressApprovalBridge` binds the Approval to the exact destination, target posture, effective data
classification, outbound payload SHA-256, resource type, capability, Project/Task/Run context,
egress-policy version, exact EgressProfile revision/source revision and cost class. The existing #15
expiry applies. A changed payload, destination, scope or policy/profile revision produces a different
`ProposedAction.digest`, so the old Approval is stale automatically. Approval decisions themselves
still go through `AuthorizationGate.decide_approval`.

The documented reuse semantic is `exact_digest_until_expiry`; deployments that need one-shot
semantics should add that as a stricter #15 policy rather than weakening the egress binding.

## Decisions and audit evidence

`EgressGate` is the single enforcement seam. The policy returns an `EgressDecision`; only `ALLOW`
is executable. `DENY`, `REQUIRE_APPROVAL`, `LOCAL_ONLY` and `UNKNOWN_BLOCKED` remain non-executable
unless the configured higher-level mechanism returns a new exact valid decision.

The gate emits value-free `EgressEvaluated` followed by `EgressAllowed` or `EgressDenied`. Audit
records can contain canonical target identity, effective classification, payload digest, policy
version, profile revision, cost class, correlation/task/run/capability references, Approval reference
and safe policy metadata. Raw prompt text, connector arguments, file bytes, secret material and
protected payload values must never be copied into egress audit events.

## Control Plane

`egress-profiles` exposes safe stable/current and exact-revision views. Lifecycle commands are:

- `egress-profile.create`;
- `egress-profile.version`;
- `egress-profile.verify`;
- `egress-profile.enable`;
- `egress-profile.disable`.

Create/version cannot set `verified`; the explicit verify transition is required. Arbitrary profile
metadata values are not projected into operator-facing resources. The projection exposes known safe
fields, the explicit sensitive-egress boolean and metadata keys only.

`egress-policy.evaluate` is a digest-only inspection command for explaining compatibility. It accepts
canonical target identity, Project scope, data classification, resource/action references and an
already-computed payload digest; it never accepts the protected outbound body merely to explain the
decision.

## Portability

The standalone `EgressProfilePortableCodec` exports the stable definition and full exact revision
history with canonical target dependencies. Import is intentionally asymmetric and fail-closed:
source-system `verified` trust is downgraded to `unverified`, verification metadata is stripped and
`allow_sensitive_external` is removed. The target deployment must explicitly re-verify the imported
profile before external trust assertions can become authoritative.

`EgressProfileImportMutationHandler` rebinds ownership to an explicitly supplied target owner and
supports compensation of a partially applied history. Imported policy therefore cannot carry source
identity or trust authority into the destination deployment.
