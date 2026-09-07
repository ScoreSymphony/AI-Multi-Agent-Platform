# Canonical reusable workflows

Issue #364 adds a platform-owned reusable workflow-definition domain. A Workflow stores
durable execution intent that can be referenced by Templates, portability and later runtime
admission without making one orchestrator, provider or Template subsystem authoritative.

## Boundary

A reusable `WorkflowDefinition` is **not** a task-bound `Plan`.

- `WorkflowDefinition` owns a stable `workflow_*` identity and points at the latest immutable
  `WorkflowRevision`.
- `WorkflowRevision` stores reusable stages, parameters, requirements, scope, provenance and
  compatibility metadata.
- canonical `Plan` and `Step` objects remain #6 runtime state tied to one canonical Task.
- orchestrator-private plans, sessions and handles never become reusable workflow identity.
- Templates may package workflows, but Template identity is not workflow identity.
- Automation triggers may start work later, but trigger/delivery state is not workflow state.

## Revision model

Revision 1 is created together with the stable definition. Every update appends exactly one
new immutable revision. Older revisions remain resolvable by `WorkflowRevisionRef` and are not
rewritten when the workflow changes.

`JsonWorkflowRepository` persists the complete definition/revision history using atomic file
replacement. Restart restore rejects unsupported repository schema versions, orphan revisions,
non-contiguous histories and definition/revision mismatches.

## Workflow content

A revision contains:

- name and description;
- one or more locally identified stages;
- dependency edges between stages;
- declared parameters/placeholders, including SecretReference placeholders;
- capability and tool requirements;
- optional exact Agent or Agent Team revision references;
- optional model-routing-policy references;
- permission requirements;
- provenance and compatibility metadata.

Stage IDs are local to the reusable revision. They are not canonical runtime `Step` IDs.
Creation validates unique stages and parameters, known dependency/parameter references, and an
acyclic dependency graph.

## Security and scope

`AuthorizedWorkflowService` is the #15 enforcement boundary for workflow management and
admission. Existing resource checks derive Project and Organization scope from the stored
`WorkflowDefinition`; caller-supplied `OperationContext.project_id` cannot replace that stored
scope. Create, read, revise and execute/admission operations are evaluated through the canonical
`AuthorizationGate`.

Canonical workflow persistence rejects known plaintext-secret and backend-private runtime
metadata keys such as provider/orchestrator session IDs, active Run IDs, credentials and access
tokens. Secret parameters declare references only; parameter values are not persisted in the
workflow definition.

## Admission into task-bound execution

`WorkflowService.admit()` resolves one exact `WorkflowRevisionRef` and creates a fresh canonical
`Plan` plus fresh canonical `Step` IDs for one Task. Dependency edges are translated from local
stage IDs to the newly allocated Step IDs. Plan/Step provenance records the exact source workflow
revision.

Admission validates declared required parameters but deliberately does not persist supplied
parameter values into reusable workflow state. The source revision is never mutated by
admission.

This keeps lifecycle ownership clear:

```text
WorkflowDefinition @ exact revision
            |
            | admission
            v
Task -> Plan -> Steps -> Runs
```

The reusable definition describes intent; #6 remains authoritative for execution lifecycle.

## Integration boundary

This domain is the canonical owner required by the `workflow_plan` Template type in #78 and by
workflow portability in #79. Those systems should call this repository/service boundary rather
than create Template-private or portability-private workflow persistence. Distribution through
#81 can reference exact workflow revisions later without changing workflow identity semantics.
