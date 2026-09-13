# Explicit Control Plane composition

Issue: #982  
Builds on: #723, #32

The canonical Control Plane has one extension mechanism for later platform domains: explicit module registration. Domain behavior remains in the domain service or an adapter around that service; Python inheritance is not a domain-ownership mechanism.

## Boundary

The focused service decomposition introduced by #723 remains unchanged. `control_plane.service.ControlPlane` continues to own the stable foundation façade and delegates to its focused Task/Run, scope, event, health, authorization and model components.

Later domains expose northbound behavior through `ControlPlaneModule`:

```python
ControlPlaneModule(
    name="example-domain",
    resource_services={"widgets": widgets},
    command_handlers={"widget.refresh": refresh_widget},
    routes=(special_route,),
    openapi_contributors=(augment_openapi,),
    requires=frozenset({"another-domain"}),
)
```

A module can own only northbound contributions:

- canonical resource collections;
- canonical commands;
- exact special HTTP routes that do not fit the generic collection/command mapping;
- OpenAPI additions for those special routes;
- explicit dependencies on other registered modules.

It is not a service locator, dependency-injection framework, second router, second command bus or orchestration layer.

## Ownership and startup validation

Every registered resource collection, command and exact special route has one owner label. Ownership is inspectable through the Control Plane registry.

Composition validates a complete module batch before installing it:

1. module names must be unique;
2. required modules must already exist or be present in the same batch;
3. collection names and command names must satisfy the canonical naming rules;
4. duplicate collection, command or route ownership fails before the batch mutates registry state;
5. installation is sorted by module name after validation, so caller order does not silently alter semantics.

Constructor-supplied legacy resource/command registrations receive the explicit owner `constructor`. Direct compatibility registrations receive `manual`. New platform domains must use named modules instead.

## HTTP and OpenAPI contributions

Generic collections and commands continue to use the existing `/api/v1` mapping. A module may register an exact route only when that generic mapping is insufficient. Exact routes are keyed by normalized HTTP method and absolute path and are subject to the same duplicate-owner validation.

OpenAPI contributors mutate the generated document only after the base schema and generic registered-resource/command paths have been created. Contributors run in deterministic module-name order.

Request IDs, correlation IDs, authentication, authorization, idempotency and canonical error mapping remain transport/application concerns of the existing Control Plane. Module registration does not bypass those boundaries.

## Compatibility façade rule

A historical `ControlPlane` class may remain temporarily when external or internal imports require that symbol. Such a compatibility façade may:

- accept the historical constructor arguments;
- create a domain binding/module from explicit dependencies;
- register that module;
- expose compatibility properties that return the explicitly supplied service/binding.

It must not reimplement domain commands, maintain a second resource/command registry, override generic dispatch to accumulate domain behavior, or participate in canonical multiple-inheritance domain composition.

Architecture tests reject every new or changed multiple-inheritance stack on a `ControlPlane` or `ControlPlaneHTTP` façade unless it is an explicitly reviewed implementation-only compatibility path. They also reject new subclasses that claim domain resources or commands through `self.register_*`. The reviewed compatibility lists are exact, so they cannot silently expand.

## Migration inventory

The #982 ownership audit found several places where independent later domains or domain registration were accumulated through the Control Plane inheritance graph.

| Pre-#982 composition | Risk | #982 state |
| --- | --- | --- |
| `ApprovalControlPlane + PortabilityControlPlane` in the single-node product composition | two domain parents; MRO decided initialization and registration order | Portability is an explicit `portability` module; canonical composition has one Control Plane base |
| `PluginControlPlane + TerminalControlPlane` | two domain parents; plugin/terminal behavior coupled through MRO | Plugin lifecycle is an explicit `plugins` module and Terminal is an explicit `terminal` module; canonical plugin/terminal composition has one Control Plane base |
| `ConversationControlPlane + NotificationControlPlane` | two independently evolving later-domain parents shared registration and command-dispatch state through cooperative MRO | Conversations are an explicit `conversations` module; Notifications publish one explicit `notifications` owner; the current Conversation façade has one Control Plane base |
| `AutomationControlPlane + progressive Search` | Automation collections/commands entered the canonical path through a domain subclass and a multiple-inheritance hardening layer | canonical authorization imports `automation_explicit_composition`; Automation is an explicit `automation` module above the linear Search checkpoint composition |
| Goals, Decision Records and Governance in the product constructor | direct registrations had anonymous/manual ownership rather than domain ownership | named `goals`, `decision-records` and `governance` modules |
| `portability_api.ControlPlane` | commands/resources and conflict guards lived in a subclass | compatibility façade only; domain behavior lives in `portability_module.py` |
| `plugin_api.ControlPlane` | lifecycle commands/resources and conflict guards lived in a subclass | compatibility façade only; domain behavior lives in `plugin_module.py` |
| focused #723 service façade | ordinary implementation façade, not a later-domain composition mechanism | preserved |

Reviewed historical implementation classes may still exist for compatibility or focused tests, but they are no longer imported as canonical ownership boundaries. The architecture guard records their exact shape and rejects new domain-composition stacks.

Any later domain that adds a resource, command or special route must register an explicit module rather than adding another domain-composition superclass.

## Current explicit domain examples

### Automation

`automation_explicit_composition.ControlPlane` keeps `AutomationService` and `ReferenceScheduler` as the behavior/runtime owners and installs the `automation` module. The module owns:

- `automations`;
- `automation-deliveries`;
- the complete canonical `automation.*` command vocabulary.

Object-scoped authorization, change-actor audit context, creation idempotency context, Search owner metadata and runtime replay semantics remain in their existing boundaries. The canonical authentication stack no longer imports the historical Automation/Search multiple-inheritance hardening class.

### Portability

`portability_control_plane_module(workflow)` owns:

- `portability-packages`;
- `portability-import-previews`;
- `portability-import-reports`;
- `portability.export`;
- `portability.package.validate`;
- `portability.preview`;
- `portability.import`.

The `PortabilityWorkflowService` remains the behavior owner.

### Plugin lifecycle

`PluginControlPlaneBinding` adapts `PluginRegistry`, optional `PluginCatalog` and the permission resolver into the `plugins` module. The module owns:

- `plugins`;
- optionally `plugin-candidates`;
- the existing `plugin.*` lifecycle command vocabulary.

`PluginRegistry` and `PluginCatalog` remain the lifecycle/discovery owners.

### Conversations

`conversation_control_plane_module(...)` adapts `ConversationService` and its current Agent/File/Knowledge dependencies into the `conversations` module. It owns:

- `conversations`;
- `conversation-messages`;
- `conversation-exports`;
- the canonical `conversation.*` command vocabulary, including waiting-task resume and retention commands.

The existing resource-aware authorization remains inside the canonical Conversation handlers rather than being replaced by a generic command-name preflight. Task lifecycle ownership remains with the kernel; the current façade only preserves the established cross-domain Task/Conversation linkage adapter and transport-specific streaming/ergonomic routes.

### Notifications and Terminal

The completed Notification implementation publishes `notifications`, `notification-preferences`, its command vocabulary and the Notification SSE transport route under the `notifications` owner. Terminal publishes `terminal-sessions` and its command vocabulary under `terminal`; Terminal WebSocket transport and project/workspace policy remain transport/application concerns rather than a second resource registry.

## Contributor rule

For a new Control Plane domain:

1. define/retain the canonical domain service outside the Control Plane façade;
2. construct resource projections and command handlers around explicit service dependencies;
3. return a named `ControlPlaneModule`;
4. register the module at the composition root;
5. add ownership/conflict tests and API/OpenAPI parity tests;
6. do not subclass the canonical façade merely to accumulate the domain.

If a domain needs semantics that the module contract cannot express, extend the narrow contract explicitly and test the ownership rule rather than creating a parallel composition mechanism.
