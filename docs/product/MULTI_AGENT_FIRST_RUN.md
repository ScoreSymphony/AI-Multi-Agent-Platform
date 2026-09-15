# Official multi-agent first run

Issue #894 makes the existing #889 reference multi-agent runtime the supported product first-run
workflow. The onboarding layer remains a thin product composition over canonical platform state; it
does not introduce a second planner, executor, lifecycle, Result, Artifact, or verification model.

## Product promise

After the platform is running, the minimum successful path is:

```text
local/self-hosted text model
        ↓
Project + Workspace
        ↓
non-trivial Goal
        ↓
canonical Task + Plan
        ↓
researcher ─┐
            ├─→ developer execution → reviewer
 developer ─┘
        ↓
Runs + Results + Artifact
        ↓
review status + inspectable trace
```

The built-in team contains three scoped, editable canonical Agent definitions with the roles
`researcher`, `developer`, and `reviewer`. The #889 planner creates four Steps: research and approach
can progress independently, execution depends on both, and review depends on execution.

This baseline does **not** require Hermes, Forge, LiteLLM, MCP, a paid API, or the standard Agent
catalog. Capabilities and optional adapters can be added later through their normal platform
boundaries; they are not hidden prerequisites for proving that the multi-agent product works.

## Prerequisites

1. Start the normal single-node platform.
2. Authenticate as a local user.
3. Configure at least one healthy text-capable Model with location `local` or `self_hosted`.
4. Create or open one canonical Project and Workspace.

When multiple owned Projects or Workspaces exist, select the intended IDs explicitly. The command
returns candidate IDs rather than guessing a scope.

## Web

Open `/onboarding`. Once a usable model, Project, and Workspace exist, the primary card is
**Official first run: multi-agent goal**. Submit a goal that benefits from research, an independent
approach, execution, and review.

On success the page exposes:

- Task and Plan identity;
- every Step, its dependency edges and terminal state;
- the exact Agent revision assigned to each Step;
- Run and Result links;
- produced Artifact IDs;
- reviewer status and any canonical Verification records that exist;
- the Task → Plan → Step trace needed for deeper inspection.

The General Assistant setup and `onboarding.run-first-task` remain available as a compatibility path,
but they are no longer the definition of product first-run completion.

## CLI

Inspect readiness first:

```bash
platform onboarding status
```

Then run the same canonical workflow used by Web and the Control Plane:

```bash
platform onboarding run-multi-agent \
  --objective "Research two viable approaches, choose one, produce a concise result, and review it." \
  --project-id <project_id> \
  --workspace-id <workspace_id>
```

`--project-id` and `--workspace-id` can be omitted when exactly one owned candidate exists. Add
`--idempotency-key <key>` when an operator needs retry-stable command identity.

## Control Plane

Command:

```text
onboarding.run-multi-agent-golden-path
```

Resource reference:

```text
first-run
```

Example payload:

```json
{
  "resource_ref": "first-run",
  "title": "First multi-agent goal",
  "objective": "Research two viable approaches, choose one, produce a concise result, and review it.",
  "project_id": "project_...",
  "workspace_id": "workspace_..."
}
```

The response is the shared product projection used by supported clients. It includes `task_id`,
`plan_id`, scoped Agent revisions, ordered Steps with dependency/run/result data, `result_ids`,
`artifact_ids`, review/verification state, and trace identifiers.

## Retry and failure behavior

The command derives all internal mutation keys from the incoming idempotency key. Replaying the same
request therefore reuses the canonical Task, proposal/Plan, scoped role Agents, and summary Artifact
instead of creating duplicate first-run state.

Expected actionable failures include:

- no usable local/self-hosted text Model: configure or revalidate one in Onboarding;
- multiple owned Projects or Workspaces without an explicit selection: choose one of the returned
  candidate IDs;
- a selected scope not owned by the authenticated actor: use an owned Project/Workspace;
- plan validation failure: inspect validation errors and repair local Model/Agent configuration.

The workflow never falls back silently to a remote or paid provider.

## Verification semantics

The #889 reviewer Step is the baseline review check for this zero-extra-configuration journey. If
canonical Verification policies are also configured, their durable records are returned in the same
projection under `verification.canonical_records`. This keeps the first-run path minimal while still
surfacing stronger policy-based verification when the operator has enabled it.

## Acceptance evidence

`tests/e2e/onboarding/test_multi_agent_first_run.py` is the product acceptance fixture. It builds the
public single-node composition, configures only a local OpenAI-compatible test model, creates a
Project and Workspace, and invokes the public Control Plane command without installing Hermes,
Forge, LiteLLM, the standard Agent catalog, or a paid provider. The test asserts:

- three specialized Agent roles and four canonical Steps;
- the expected parallel fan-in dependency graph;
- successful Runs and one Result per Step;
- a visible summary Artifact;
- successful reviewer state and inspectable trace IDs;
- retry-stable command behavior without duplicate Agents or Plan state.

The lower-level #889 tests remain authoritative for planner DAG construction, concurrent frontier
dispatch, handoff consumption, and exact Agent revision binding. The #894 acceptance fixture proves
that the same runtime is reachable as an ordinary product first run rather than only as an internal
integration scenario.
