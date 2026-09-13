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
    command_authorizers={"widget.refresh": authorize_refresh},
    command_observers=(audit_refresh,),
    routes=(special_route,),
    openapi_contributors=(augment_openapi,),
    requires=frozenset({"another-domain"}),
)
```

A module can own only northbound contributions:

- canonical resource collections;
- canonical commands;
- command-specific authorization adapters;
- post-success command observers for projections/audit integration;
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

## Command dispatch, authorization and observers

Registered commands use one registry-aware dispatch boundary. The dispatcher:

1. validates the canonical command name and idempotency requirement;
2. resolves the one registered command owner/handler;
3. invokes the module-owned authorizer when one is declared, otherwise preserving the exact-payload default authorization binding;
4. invokes the canonical handler;
5. validates that the result does not expose private payload state;
6. invokes registered post-success observers in deterministic module-name order.

This is important for domains whose authorization cannot be represented as a generic command/resource check. Task management, Conversations, Organization collaboration and Task Project reassignment keep their existing relationship- or payload-aware authorization semantics in explicit authorizers/handlers instead of relying on an inherited `execute_command` override.

Observers are projections/integration hooks, not a second dispatch mechanism. Organization ownership mirroring and Organization audit projection use observers after the canonical command has succeeded.

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

Architecture tests reject every new or changed multiple-inheritance stack on a `ControlPlane` or `ControlPlaneHTTP` façade unless it is an explicitly reviewed implementation-only compatibility path. They also reject new subclasses that claim domain resources or commands through `self.register_*`, and they pin completed linear migrations so Organization and Task Project reassignment cannot regain private dispatch/registration overrides.

## Migration inventory

The #982 ownership audit found several places where independent later domains or domain registration were accumulated through the Control Plane inheritance graph.

| Pre-#982 composition | Risk | #982 state |
| --- | --- | --- |
| `ApprovalControlPlane + PortabilityControlPlane` in the single-node product composition | two domain parents; MRO decided initialization and registration order | Portability is an explicit `portability` module; canonical composition has one Control Plane base |
| `PluginControlPlane + TerminalControlPlane` | two domain parents; plugin/terminal behavior coupled through MRO | Plugin lifecycle is an explicit `plugins` module and Terminal is an explicit `terminal` module; canonical plugin/terminal composition has one Control Plane base |
| `ConversationControlPlane + NotificationControlPlane` | two independently evolving later-domain parents shared registration and command-dispatch state through cooperative MRO | Conversations are an explicit `conversations` module; Notifications publish one explicit `notifications` owner; the current Conversation façade has one Control Plane base |
| `AutomationControlPlane + progressive Search` | Automation collections/commands entered the canonical path through a domain subclass and a multiple-inheritance hardening layer | canonical authorization imports `automation_explicit_composition`; Automation is an explicit `automation` module above the linear Search checkpoint composition |
| `RunWorkspaceControlPlane + TaskManagementControlPlane` | independent Workspace/Run and Task-management branches were combined in the canonical Search ancestry; MRO selected command and initialization behavior | the historical `workspace_task_management_api` path is behavior-free; the linear Workspace/Run composition has one domain base and `task-management` explicitly owns its command vocabulary |
| `organization_runtime_composition.ControlPlane` | Organization commands/resources and optional Accounting projections were registered through a later subclass; Organization authorization and ownership mirroring depended on its `execute_command` override | `organization_runtime_composition` is a behavior-free shim; `organizations` and `accounting` are named modules; Organization scope authorization is module-owned and ownership mirroring is a post-success observer |
| `task_project_reassignment.ControlPlane.execute_command` | Task Project commands were selected by a later inherited dispatcher and guarded by a private conflict override | `task-project-reassignment` explicitly owns both move commands and their OpenAPI contribution; relationship-aware authorization remains in the handlers |
| `release_api.ControlPlaneHTTP` special-case route | the release status endpoint and OpenAPI contribution were owned implicitly by an HTTP subclass override | `release-status` explicitly owns `GET /api/v1/release/status` and its OpenAPI contribution; the HTTP façade only preserves root-manifest compatibility and the operator property |
| Goals, Decision Records and Governance in the product constructor | direct registrations had anonymous/manual ownership rather than domain ownership | named `goals`, `decision-records` and `governance` modules |
| `portability_api.ControlPlane` | commands/resources and conflict guards lived in a subclass | compatibility façade only; domain behavior lives in `portability_module.py` |
| `plugin_api.ControlPlane` | lifecycle commands/resources and conflict guards lived in a subclass | compatibility façade only; domain behavior lives in `plugin_module.py` |
| focused #723 service façade | ordinary implementation façade, not a later-domain composition mechanism | preserved |

Reviewed historical implementation classes may still exist for compatibility or focused tests, but they are no longer imported as canonical ownership boundaries. The architecture guard records their exact shape and rejects new domain-composition stacks.

Any later domain that adds a resource, command or special route must register an explicit module rather than adding another domain-composition superclass.

## Current explicit domain examples

### Task management and Task Project reassignment

The historical `workspace_task_management_api` module is a behavior-free compatibility import. Canonical composition uses a linear Workspace/Run implementation path and installs a named `task-management` module that owns:

- `task-management.update`;
- `task-management.bulk-update`;
- the Task-management OpenAPI additions.

The `TaskManagementService` remains the behavior owner. The module's explicit command authorizer defers to the existing hardened handlers so exact-payload authorization, per-Task scope checks and idempotency semantics remain unchanged; module registration does not replace those checks with a generic command-name authorization preflight.

`task-project-reassignment` independently owns:

- `task.project.move`;
- `task.project.bulk-move`;
- their specialized OpenAPI contract.

The `TaskProjectReassignmentService` remains the lifecycle/relationship owner, and its handlers continue to authorize the Task plus source/destination Project scopes before moving state.

### Organization and Accounting

`organization_explicit_composition.ControlPlane` adapts `OrganizationService` into the `organizations` module. That module owns the Organization, Team, Membership, Invitation, resource-ownership, resource-share and external-group-mapping collections plus the Organization management command vocabulary.

Organization command authorizers preserve the existing organization/team scope checks and explicit cross-organization share permission. `CanonicalOwnershipMirror` remains an integration service; it observes successful commands and mirrors canonical owners without becoming a second command bus.

Optional Accounting projections are installed through the independent `accounting` module. The same `AccountingService` instance continues to serve the existing threshold/evaluation integrations; #982 changes only northbound ownership, not accounting authority.

`organization-audit` declares an explicit dependency on `organizations`, owns `organization-audit-events`, and records successful Organization mutations as a post-success projection.

### Release status

`release-status` owns the exact special route `GET /api/v1/release/status` and the associated OpenAPI policy contribution. The mutable `ReleaseOperatorService` remains the behavior/state owner. Authentication stays outside the module at the existing authenticated HTTP boundary.

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
