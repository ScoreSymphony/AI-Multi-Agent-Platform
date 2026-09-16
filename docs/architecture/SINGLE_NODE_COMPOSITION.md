# Single-node composition architecture

The supported single-node deployment is assembled as explicit, ordered composition layers rather than by one construction function that owns every subsystem.

## Base composition

`deployment.single_node.build_single_node_deployment()` is the lower-level reference composition root. It orchestrates typed builders under `deployment/composition/` and returns the stable `SingleNodeDeployment` surface.

The base order is:

1. **Storage** — kernel/event persistence, scopes, files, workspaces, repository catalog/provenance and run-workspace bindings.
2. **Observability** — exporter and telemetry.
3. **Security** — authentication, authorization, approvals, audit and optional protected secret access.
4. **Runtime services** — Agents, conversations, capabilities, model registry/routing, Model Runtime and onboarding.
5. **Platform services** — workflows, research, templates and capability assignments.
6. **Repository foundation** — repository service, management, workspace execution coordination and event ingress.
7. **Execution** — reference/distributed lifecycle selection and orchestration, exposed to the kernel through a stable startup lifecycle binding.
8. **Verification and kernel** — verification persistence/completion authority, canonical kernel, coordination and first-run task service.
9. **Repository runtime** — run integration after the kernel exists.
10. **Evaluation** — durable evaluation composition against the already-created runtime and kernel.
11. **Health** — required provider readiness aggregation.
12. **Control Plane and HTTP** — canonical registrations followed by authenticated HTTP/ASGI exposure.

Builder inputs and outputs are explicit dataclasses. Dependencies flow forward; builders do not resolve unrelated services through a global container.

## Durable public composition

`deployment.durable_connectors.build_single_node_deployment()` adds the durable product-facing domains used by the normal server profile. It remains a second, intentionally thin orchestration root because connector discovery and the shared durable EgressGate must exist before later runtime layers are composed.

Its ordered builders are:

1. **Pre-runtime foundation** — storage, observability and security are built without constructing Model Runtime.
2. **Connector foundation** — durable connector repository, registry and repository-discovery resolver.
3. **Egress foundation** — the shared durable egress policy runtime is created from the already-built security and telemetry authorities.
4. **Base deployment continuation** — runtime, repository, execution, Verification/kernel/Evaluation, Control Plane and HTTP composition continue from the existing foundation; `ModelRuntime` is constructed directly through the shared egress binding and connector-aware repository discovery is injected explicitly.
5. **Automatic review** — reviewer workflow and restart reconciliation consume the public verification-completion authority.
6. **Connector services** — Connector invocation and egress Control Plane surfaces bind to the already-created shared EgressGate.
7. **Application distribution** — release persistence, build lifecycle/kernel, release-gate coordinator and optional GitHub release publisher are constructed explicitly when their dependencies exist.
8. **Planning** — durable planning repository, proposal kernel, binding coordinator and replanning evidence bridge.
9. **Durable extensions** — Context finalizes the stable startup lifecycle binding, then Learning and Handoffs join after Planning and egress exist. The kernel's private lifecycle field is never replaced after construction.
10. **Template environment refresh** — connector inventory joins the existing template environment.
11. **Explicit deployment promotion** — the base deployment fields are copied explicitly into the public durable deployment surface together with durable-domain fields.

Optional adapters remain gated at their owning builder. A missing optional secret provider does not create the GitHub release provider, and distributed application execution is created only when distributed execution is enabled and a distributed runtime exists.

## Ownership boundaries

The builders are composition owners, not new domain owners. Canonical persistence and behavior stay in their existing domain packages. The Control Plane remains the single northbound registration authority, and `PlatformKernel` remains the canonical Task/Run lifecycle owner.

Composition code may depend on public domain services, repositories, protocols and typed deployment outputs. It should not inspect another layer's private fields to discover construction state. Public outputs such as the verification completion authority, pre-authorization lifecycle and startup lifecycle binding are carried on the deployment or builder bundle when a later layer needs them.

## Change rules

When adding a subsystem to the single-node profile:

- place construction in the narrowest cohesive builder;
- make new dependencies visible in the builder signature;
- preserve forward dependency direction;
- avoid post-hoc private-attribute replacement;
- keep optional providers optional at import and construction time;
- add focused builder coverage and retain full startup/restart/smoke coverage.

If a new dependency cannot be represented without reaching into another subsystem's private state, add a narrow public construction seam at the owning domain boundary before wiring it into composition.
