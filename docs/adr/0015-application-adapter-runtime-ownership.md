# ADR 0015: Own managed application lifecycle in `applications`

- Status: Accepted
- Date: 2026-09-16
- Issue: #1173

## Context

Issue #1173 introduces declarative Application Adapters for independently deployable external applications and multi-service stacks. The repository already contains several nearby but intentionally narrower ownership boundaries:

- `application_distribution` owns application build/distribution state and release-gate integration;
- `distribution` owns component registry/catalog discovery, validation, installation and distribution;
- `distributed` owns Node/Worker scheduling and distributed-runtime semantics;
- `deployment` owns self-hosted platform deployment composition and process entrypoints;
- `security` owns authentication, authorization, approvals and secret-reference semantics.

None of those domains owns the desired/observed lifecycle of an installed external application instance. Reusing one of them would either mix release/distribution state with runtime lifecycle, create a second scheduler, or make deployment/provider details canonical.

## Decision

Create a dedicated canonical `applications` domain for managed external application definitions and instances.

`applications` owns:

- the provider-neutral Application manifest and schema;
- canonical installed `Application` definitions;
- canonical `ApplicationInstance` identity;
- declared services/components, dependencies, endpoints, volumes, configuration and secret references;
- desired state, observed state and aggregate/per-service health;
- the platform-owned `ApplicationRuntime` contract;
- lifecycle operations and recovery/reconciliation semantics;
- canonical endpoint resolution and application logs/diagnostics contracts.

Concrete backends remain replaceable runtime implementations behind that contract. Docker Compose, Podman Compose, local-process, Kubernetes or remote-worker details must not leak provider-private identifiers or backend-specific lifecycle semantics into canonical public identity.

The ownership boundaries remain:

- `application_distribution`: build artifacts, release state and release gates;
- `distribution`: registry/catalog/Marketplace discovery and installation dispatch;
- `distributed`: Node/Worker capacity, placement and scheduling authority;
- `deployment`: platform process composition and self-hosted deployment wiring;
- `security`: authorization, approval and secret material/reference authority;
- `applications`: installed external-app definition, instance lifecycle, health, endpoints and reconciliation.

Application placement must consume existing distributed scheduling contracts rather than create a parallel scheduler. Application secret bindings must use canonical `SecretReference` values rather than introduce a second secret store. Application storage/workspace bindings must reference platform-managed resources rather than unrestricted host paths.

## Canonical identity

An Application has a stable platform application ID and an installed runtime-adapter identity. An Application Instance has its own stable canonical instance ID. Backend-private container IDs, process IDs, Compose project names, Kubernetes pod IDs and equivalent provider handles are implementation details and are never canonical public authority.

Logical endpoint references such as `web.web` are canonical declarations. A runtime resolves them to concrete URLs/addresses at execution time; fixed host ports are not canonical identity.

## Consequences

This introduces a new durable domain owner because application lifecycle remains meaningful independently of any concrete backend and cannot be assigned to an existing owner without mixing lifecycle/state responsibilities.

Registry/Marketplace integration remains a composition over `distribution`; #1173 does not redesign the generic catalog. Control Plane and frontend surfaces consume canonical `applications` resources and must not embed app-specific or backend-specific lifecycle logic.

The first concrete runtime backend may be implementation-specific, but conformance must prove that the canonical manifest and lifecycle contract remain usable without exposing backend-private IDs or requiring Docker semantics in the public model.
