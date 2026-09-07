# Standard Agents and Starter Agent Teams

Issue #77 provides an optional starter catalog on top of the canonical Agent contracts from
issue #33. The catalog is convenience configuration, not a second runtime architecture.
Every installed starter is a normal `AgentProfile` or `AgentTeamProfile` and therefore uses
the same model routing, capability, authorization, memory, persistence, revision and
orchestration boundaries as user-created Agents.

## Design invariants

- The catalog is provider-neutral. No starter names or requires a particular LLM vendor,
  model server, orchestrator, executor, host, operating system or memory backend.
- Model selection is expressed through the canonical issue #10 `RoutingRequirements` and
  remains replaceable by the deployment or user. The initial profiles require only text
  modality and do not require native model tool-calling merely because optional platform
  capabilities exist.
- Tools are expressed only as canonical issue #12 capability IDs. A starter never calls a
  provider implementation directly.
- Authorization and approval remain authoritative in issue #15 and the issue #33 runtime.
  A starter profile cannot grant itself permissions.
- Bundled IDs are stable and bootstrap is idempotent. Re-running bootstrap never writes a
  new revision over an existing bundled identity.
- Starter teams pin exact Agent revisions. A local Agent edit therefore cannot silently
  alter the meaning of an already-installed Team revision.
- A user-owned clone is an ordinary canonical Agent/Team. It can be edited, disabled and,
  while no historical Team/run references require it, deleted through the normal repository
  lifecycle.
- Catalog discovery is separate from installation. A deployment can expose the starter
  catalog without installing any bundled Agent identity.

## Catalog version and provenance

The initial catalog version is `1.0.0` with source
`ai-multi-agent-platform.standard-agents`. Each installed starter records:

- stable canonical Agent or Team ID;
- starter key and kind;
- starter catalog version and source;
- platform package release at which the definition is bundled;
- permission-profile description;
- changelog text and migration note;
- canonical creation provenance.

The catalog version is the version of the starter artifact itself. It is intentionally
separate from provider versions and model versions. `platform_release` records the package
release that bundled the definition; catalog migrations are still governed by the catalog
version and explicit migration policy below.

## Standard Agents

| Starter | Default purpose | Capability posture | Default state |
| --- | --- | --- | --- |
| General Assistant | General user tasks | Optional web/file read; shell denied | Enabled |
| Planner | Goal decomposition and planning | No write/shell execution | Enabled |
| Researcher | Source-based research | Optional web/file read; write/shell denied | Enabled |
| Developer | Scoped software development | File read required; file write and shell optional; shell approval-bound | Enabled |
| Reviewer | Independent review/testing | Optional file read; write/shell denied | Enabled |
| Data Analyst | Structured-data analysis | Optional data/file read; write/shell denied | Enabled |
| File Assistant | Scoped file work | File read required; file write optional; shell denied | Enabled |
| System Administrator | Privileged operations | Shell required; writes optional; privileged operations approval-bound | **Disabled** |

The capability IDs describe requested platform semantics, not installed tools. A deployment
may satisfy them through native tools, MCP, another provider, or a future adapter.

### Explicit scope for file-facing starters

The managed starter workflow marks `Developer` and `File Assistant` as explicitly scoped
starters. `Software Development Team` is scoped for the same reason.

A clone created through `standard-agent.clone` for either scoped Agent must include a
canonical `project_id` or `workspace_id`. `standard-agent-team.clone` applies the same rule to
the Software Development Team. Missing scope fails with `invalid_request` instead of
silently producing a supposedly workspace-restricted starter with no assigned scope.

This is deliberately starter-specific. The general #33 Agent contracts remain capable of
representing unscoped custom Agents where a deployment explicitly chooses that design.
Actual file access is still granted by the canonical capability/authorization/provider path;
an Agent's requested file capability is not itself a filesystem grant.

### Required versus optional capabilities

`assess_standard_agent_capabilities()` compares a starter with the canonical capability
inventory:

- missing **optional** capabilities are reported but do not make the starter capability-
  incompatible;
- missing **required** capabilities are reported explicitly;
- `ensure_standard_agent_capabilities()` fails with `unsupported_capability` when a required
  capability is absent instead of silently selecting an unrelated provider tool.

Runtime authorization, worker placement, model suitability and provider health remain
separate checks and are not replaced by this readiness result.

## Starter Agent Teams

### Software Development Team

- Planner — leader and task decomposition;
- Developer — implementation;
- Reviewer/Tester — independent verification.

The Planner may delegate to Developer or Reviewer; the Developer may delegate to Reviewer.
The initial Team allows two Agents to run in parallel and caps coordination at 16 steps.
Managed clones require an explicit project/workspace scope.

### Research Team

- Researcher — leader and source investigation;
- Reviewer — source checking and independent review;
- Data Analyst — analysis/writing role.

The Researcher may delegate to Reviewer or Data Analyst. The initial Team allows three
Agents to run in parallel and caps coordination at 16 steps.

Member Agent policies remain authoritative. Team membership does not widen the model,
capability, memory or authorization rights of a member.

## Library bootstrap API

A deployment that directly composes Python services may call:

```python
result = bootstrap_standard_agents(
    agent_service,
    capability_inventory=capability_registry,  # optional readiness report
)
```

The operation installs only missing stable catalog identities. If a catalog identity already
exists and its revision-1 provenance identifies the same starter, bootstrap preserves its
current revision unchanged. If the stable ID is occupied by a non-catalog definition,
bootstrap fails with a conflict rather than overwriting data.

## Control Plane catalog and lifecycle

`register_standard_agent_control_plane(...)` exposes the starter library through the generic,
versioned Control Plane extension seam.

Read-only catalog collections:

- `standard-agents`
- `standard-agent-teams`

The catalog collections exist even when no standard Agent has been installed. They expose
stable definition IDs, versions, capability requirements, permission posture and the
machine-readable explicit-scope requirement.

Registered commands:

- `standard-agent.bootstrap` — explicitly install missing bundled Agent/Team identities;
- `standard-agent.clone` — clone bundled revision 1 into an authenticated user-owned Agent;
- `standard-agent-team.clone` — clone bundled Team revision 1;
- `agent.delete` — delete the authenticated owner's unreferenced Agent copy;
- `agent-team.delete` — delete the authenticated owner's unreferenced Team copy.

Bootstrap uses `resource_ref=standard-agent-catalog`. The generic Control Plane still applies
its normal issue #15 authorization and idempotency boundary before these mutation handlers.
The delete handlers additionally bind deletion to the authenticated owner, so a user-owned
copy cannot be used to delete the service-owned bundled identity or another user's copy.

The generic #33 `agent.create`, `agent.update`, `agent.clone`, `agent-team.create`,
`agent-team.update` and related commands remain available. The starter-specific clone
commands add catalog identity validation and the explicit-scope guard; they do not replace
the canonical lifecycle.

## Single-node deployment

The production-shaped single-node composition now includes a durable `JsonAgentRepository`
at `db/agents.json`, registers the canonical #33 Agent Control Plane, and registers the
standard catalog integration.

It intentionally **does not auto-bootstrap standard Agents on process startup**. A fresh
single-node deployment can discover the catalog immediately and an authorized administrator
can explicitly run `standard-agent.bootstrap`. Once installed, definitions persist across
restart.

This separation matters for ownership and upgrades: if a deployment intentionally removes a
bundled default, restarting the process must not silently reinstall it. Explicit bootstrap
remains available when the deployment actually wants missing defaults restored.

## Clone and customize

The lower-level library helper remains available:

```python
copy = clone_standard_agent(
    agent_service,
    "general_assistant",
    owner_ref=current_user,
    name="Project Assistant",
)
```

For file-facing standard roles, prefer the managed Control Plane clone workflow so an explicit
project/workspace scope is recorded atomically with the clone.

A user-owned copy can use the normal issue #33 update path to change, among other things:

- model routing requirements or explicit model selection;
- allowed/denied/required capabilities;
- memory scopes and memory configuration references;
- knowledge-source policy;
- project/workspace assignment;
- instructions and role text;
- enabled/disabled state;
- Team membership and coordination limits.

The bundled source remains unchanged.

## Delete semantics

`AgentService.delete_agent(..., expected_owner_ref=...)` and
`delete_team(..., expected_owner_ref=...)` provide the repository/service ownership boundary.
The repositories reject deletion when historical Team revisions or Agent-run records still
reference the resource. `JsonAgentRepository` persists successful deletions atomically.

The Control Plane `agent.delete` and `agent-team.delete` commands use the authenticated actor's
owner identity as `expected_owner_ref`. A user therefore can delete an unreferenced user-owned
copy without deleting the bundled starter or another owner's resource.

A deployment administrator may still remove or replace bundled defaults through deployment
configuration/repository lifecycle where policy permits; dependent Team revisions must be
removed first. The Control Plane user-copy delete command deliberately does not turn a normal
user identity into the service owner of the bundled catalog.

## Security-sensitive profiles

The catalog intentionally errs toward least privilege:

- Researcher and Reviewer do not receive destructive filesystem or shell capabilities.
- File Assistant never receives shell execution; managed clones require explicit project or
  workspace assignment.
- Developer does not receive credentials or unrestricted host authority. Shell execution is
  optional, carries the standard shell approval reference, and managed clones require scope.
- System Administrator is disabled by default and its privileged shell/write paths carry the
  standard privileged-administration approval reference.

An `approval_ref` is not descriptive metadata: the issue #33 runtime validates it against the
resolved canonical `CapabilitySpec.required_approvals`. The actual invocation still passes
through the issue #15 authorization/approval path. Regression coverage includes the disabled
System Administrator profile itself, not only the Developer shell profile.

## Upgrade and migration policy

For catalog `1.0.0` there is no automatic migration. The migration rule is:

> Existing bundled identities and all user-owned clones are preserved. Re-bootstrap only
> installs definitions that are missing. No profile revision is rewritten automatically.

Future incompatible catalog changes must bump the catalog version, document the change and
provide an explicit migration decision. They must never infer that a locally modified
starter or clone should be replaced.

The single-node composition reinforces this rule by exposing catalog discovery at startup
without invoking bootstrap automatically.

## Future boundaries

Export/import and portable sharing of customized Agents belong to issue #79. Once that
portable subsystem exists, these resources require no starter-specific export format because
installed definitions and user-owned copies are ordinary canonical Agent/Team resources.
Frontend-specific catalog presentation may be added separately; the generic Control Plane
already exposes the versioned resources and mutation commands required by clients.
