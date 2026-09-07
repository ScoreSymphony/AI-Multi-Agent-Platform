# CLI capability inventory

Issue: #38
Owning domain: #12

The capability CLI is a read-only client of explicitly registered versioned Control Plane resources. It never reads `CapabilityRegistry` internals, invokes MCP directly, or treats administrative inventory as an authorization grant.

## Commands

```text
platform capability list
platform capability show CAPABILITY_ID
platform capability-provider list
platform capability-provider show PROVIDER_ID
```

All four commands use ordinary `GET /api/v1/...` requests:

- `capability` reads `/api/v1/capabilities`;
- `capability-provider` reads `/api/v1/capability-providers`.

The owning application registers those collections with `capability_resource_services(registry)`. If they are not registered, the CLI receives the normal canonical `not_found` route response; there is no local registry/provider fallback.

## Capability inventory versus discovery

Administrative inventory and caller-facing discovery intentionally have different semantics.

`CapabilityRegistry.list_capabilities()` and policy-aware discovery answer whether a capability is statically usable for a supplied permission/worker context. The new `inventory_capabilities()` method answers which canonical capability versions are registered, including permission-restricted and unavailable versions.

This distinction prevents an administrator from losing visibility into a restricted or unhealthy capability without weakening invocation authorization. Actual capability use continues through the canonical #12 invocation and policy pipeline.

A capability resource is grouped by its canonical `capability_id`; the `versions` array retains the version-specific schema, safety, side-effect, permission, approval, worker, health, availability, feature and credential-requirement metadata.

## Provider inventory

`CapabilityRegistry.inventory_providers()` returns only stable public `ProviderDescriptor` snapshots. The Control Plane does not expose provider implementation objects or private registry dictionaries.

Provider resources include the public descriptor fields such as provider type, contract version, supported operations, health and availability. Existing CLI recursive redaction remains defense in depth for all rendered output.

## Mutations and invocation

This slice deliberately does not add a generic capability invocation or provider-management command. A future mutating/test command must have an explicit canonical northbound contract and pass authorization/approval checks; the CLI must not turn the generic extension command seam into an unrestricted executor.
