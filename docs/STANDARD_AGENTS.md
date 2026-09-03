# Standard Agents and Starter Agent Teams

Issue #77 provides an optional starter catalog on top of the canonical Agent contracts from
issue #33. The catalog is convenience configuration, not a second runtime architecture.
Every starter is a normal `AgentProfile` or `AgentTeamProfile` and therefore uses the same
model routing, capability, authorization, memory, persistence, revision and orchestration
boundaries as user-created Agents.

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

### Research Team

- Researcher — leader and source investigation;
- Reviewer — source checking and independent review;
- Data Analyst — analysis/writing role.

The Researcher may delegate to Reviewer or Data Analyst. The initial Team allows three
Agents to run in parallel and caps coordination at 16 steps.

Member Agent policies remain authoritative. Team membership does not widen the model,
capability, memory or authorization rights of a member.

## Bootstrap

A deployment composes its normal `AgentService` and then calls:

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

This gives upgrades a deliberately conservative rule: **bootstrap never silently migrates
or overwrites an existing starter**. A future catalog revision must provide an explicit
migration/upgrade operation and changelog before it can alter installed definitions.
User-owned clones are separate IDs and are never bootstrap targets.

## Clone and customize

```python
copy = clone_standard_agent(
    agent_service,
    "developer",
    owner_ref=current_user,
    name="Project Developer",
)
```

`clone_standard_agent()` and `clone_standard_team()` copy bundled revision 1 into a new
canonical ID owned by the caller. The copy can then use the normal issue #33 update path to
change, among other things:

- model routing requirements or explicit model selection;
- allowed/denied/required capabilities;
- memory and knowledge-source policy;
- project/workspace scope;
- instructions and role text;
- enabled/disabled state;
- Team membership and coordination limits.

The bundled source remains unchanged.

## Delete semantics

`AgentService.delete_agent(..., expected_owner_ref=...)` and
`delete_team(..., expected_owner_ref=...)` provide an ownership-checked deletion boundary.
The repositories reject deletion when historical Team revisions or Agent-run records still
reference the resource. `JsonAgentRepository` persists successful deletions atomically.

A user therefore can delete an unreferenced user-owned copy without deleting the bundled
starter. Supplying the user's owner reference for a bundled service-owned starter fails the
owner check. A deployment administrator may still remove or replace bundled defaults through
the same canonical repository lifecycle where policy permits; dependent Team revisions must
be removed first. Higher-level API/UI deletion remains subject to issue #15 authorization.

## Security-sensitive profiles

The catalog intentionally errs toward least privilege:

- Researcher and Reviewer do not receive destructive filesystem or shell capabilities.
- File Assistant never receives shell execution and its instructions require assigned
  project/workspace scope.
- Developer does not receive credentials or unrestricted host authority. Shell execution is
  optional and carries the standard shell approval reference.
- System Administrator is disabled by default and its privileged shell/write paths carry the
  standard privileged-administration approval reference.

An `approval_ref` is not descriptive metadata: the issue #33 runtime validates it against the
resolved canonical `CapabilitySpec.required_approvals`. The actual invocation still passes
through the issue #15 authorization/approval path.

## Upgrade and migration policy

For catalog `1.0.0` there is no automatic migration. The migration note is therefore:

> Existing bundled identities and all user-owned clones are preserved. Re-bootstrap only
> installs definitions that are missing. No profile revision is rewritten automatically.

Future incompatible catalog changes must bump the catalog version, document the change and
provide an explicit migration decision. They must never infer that a locally modified
starter or clone should be replaced.

## Future boundaries

Export/import and portable sharing of customized Agents belong to issue #79. UI-specific
starter management belongs to the relevant UI work. Those layers should consume these
canonical definitions rather than inventing another starter schema.
