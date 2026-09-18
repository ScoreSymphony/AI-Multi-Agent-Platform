# Official multi-agent first run

The supported first product workflow demonstrates the platform's multi-agent value without requiring
optional orchestration stacks or paid services. It composes the existing reference multi-agent
runtime through canonical Project, Workspace, Agent, Task, Plan, Step, Run, Artifact, Result and
Verification authorities.

## What the first run proves

A successful run follows this visible shape:

```text
Goal
  |
  v
Task -> Plan
        |-- Gather authoritative evidence -------- researcher ----\
        |                                                        |
        |-- Prepare independent execution approach -- developer --+--> Produce result -- developer
        |                                                                          |
        |                                                                          v
        +-------------------------------------------------------> exact Result Verification -- reviewer
                                                                                   |
                                                                                   v
                                                                            Result + Artifact + trace
```

The research and approach branches have no dependency on one another. Execution waits for both, so
the Plan exposes real parallel-ready work and a fan-in edge. The produced Result is then subject to a
canonical Agent Verification policy using the scoped reviewer revision.

The baseline does **not** require Hermes, Forge, LiteLLM, MCP, a remote Worker, the standard Agent
catalog, or a paid model/API provider.

## Clean-checkout startup

Use the normal supported single-node deployment. From a fresh clone on Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install '.[server]'
cp config/single-node.env.example .env.single-node
set -a
. ./.env.single-node
set +a
platform-server serve
```

In a second terminal, start the Web UI:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. On a fresh installation the server-owned bootstrap state routes to
**Create your administrator account**. Creating the account in the browser installs the initial
administrator policy, establishes the canonical HttpOnly session and continues into the persistent
setup wizard. A reload or restart resumes the stored setup state instead of requiring bootstrap to be
repeated.

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and set the corresponding
`AI_MAP_*` environment variables in the process instead of sourcing the POSIX env file. The server
listens on the configured Control Plane address; the reference default is `127.0.0.1:8000`.

The installation and server commands above are the same supported single-node path documented in
[`../operations/DEPLOYMENT.md`](../operations/DEPLOYMENT.md). The first-run feature does not add a
second deployment mode.

### Operator/recovery account bootstrap

`platform-server bootstrap-admin` remains available for operator and recovery workflows when browser
bootstrap cannot be used. It is not a prerequisite for the normal browser-first startup:

```bash
platform-server bootstrap-admin --username admin
```

The password is requested without placing it in shell history. After this recovery path, start the
server normally and sign in through the browser or CLI.

## Minimum product setup

After authentication, `/onboarding` guides only the prerequisites that the official workflow needs:

1. configure or revalidate one text-capable Model at location `local` or `self_hosted`;
2. create or select one owned Project;
3. create or select one owned Workspace in that Project;
4. submit the multi-agent goal.

No General Assistant or standard Agent catalog bootstrap is required by the multi-agent command
itself. The product command creates or reuses three scoped canonical Agent revisions for
`researcher`, `developer`, and `reviewer`. The browser-first setup wizard may establish additional
standard product readiness state before enabling the dashboard; that state does not change the
runtime requirements of this command.

For an OpenAI-compatible local endpoint, the existing onboarding model setup can be used. The
provider must pass its normal health/model-inventory validation before it becomes eligible. Remote
or paid providers are never selected implicitly.

## Web workflow

Open `/onboarding`. Once a routable local/self-hosted text model and owned Project/Workspace exist,
the primary action is **Official first run: multi-agent goal**.

If several Workspaces are available, select the Workspace. The browser reads that canonical
Workspace and derives its Project instead of allowing an invalid Project/Workspace pair to be
constructed client-side.

After execution the same page exposes:

- Task and Plan identity;
- all four Steps and their dependency edges;
- exact Agent IDs/revisions assigned to each Step;
- Run and Result links;
- canonical Artifact links;
- canonical Verification records, including the exact produced Result review;
- Task/Plan/Step trace identifiers.

The existing General Assistant journey remains available as an optional single-Agent path, but it is
not the definition of first-run success for the multi-agent command.

## CLI workflow

The CLI calls the same Control Plane command used by Web. Authenticate first, then inspect onboarding
state and run the maintained scenario:

```bash
platform auth login --username admin
platform onboarding status
platform onboarding run-multi-agent \
  --objective "Research two viable approaches, produce a concise result from both inputs, and review the exact result."
```

When more than one Project or Workspace is owned, specify the intended scope explicitly:

```bash
platform onboarding run-multi-agent \
  --project-id project_... \
  --workspace-id workspace_... \
  --objective "Research two viable approaches, produce a concise result from both inputs, and review the exact result."
```

For retry-stable operator workflows, provide `--idempotency-key`. Reusing the same key and payload
reuses the canonical Task/Plan/Agent/Artifact state rather than creating another first run.

## Control Plane contract

The official command is:

```text
POST /api/v1/commands/onboarding.run-multi-agent-golden-path
Idempotency-Key: <unique-key>
```

with `resource_ref=first-run` and an `objective`. `project_id` and `workspace_id` are optional only
when the owned scope is unambiguous.

The response is the shared product projection for Web and CLI. It includes the canonical Task and
Plan, exact scoped Agents, ordered Steps and dependencies, Run/Result/Artifact IDs, Verification
records and trace IDs. The final product `result_id` identifies the Result from **Produce the
requested result**, rather than the reviewer's own analysis output.

## Verification and Artifact lifecycle

The workflow registers a task-scoped Verification policy before execution. The policy requires an
Agent verifier distinct from the producing Agent and routes review to the exact scoped reviewer
revision. Verification is therefore canonical platform state, not a UI label derived from the
review Step.

A small goal Artifact is created and attached to the Task before execution starts. This gives the
first run an immediately traceable Artifact while respecting kernel completion semantics: attaching
a new output after a Task has already passed Verification would invalidate the completion subject and
must not be used as presentation-only post-processing.

## Actionable failure states

The starter fails closed instead of silently changing architecture:

- no routable local/self-hosted text model -> configure or revalidate one through Onboarding;
- several owned Projects -> select a returned Project candidate;
- several Workspaces -> select a returned Workspace candidate;
- selected Workspace outside the selected Project -> choose the canonical matching scope;
- selected scope not owned by the actor -> choose an owned scope;
- Plan validation failure -> inspect the reported validation errors and repair model/Agent setup;
- provider health becomes unknown after restart -> run the canonical provider health revalidation,
  then retry the same idempotent goal.

There is no fallback to a remote or paid provider.

## Acceptance fixture

`tests/e2e/onboarding/test_official_multi_agent_first_run.py` exercises the public single-node
composition with only an OpenAI-compatible local test endpoint. It verifies specialized role Agents,
the #889 four-Step dependency graph, real Result Verification, Artifact visibility, replay
idempotency, restart/provider-health recovery, and actionable missing-model guidance.

Lower-level #889 runtime tests remain responsible for detailed planner, scheduling, handoff/context
and recovery mechanics. This fixture proves that those mechanics are reachable as the maintained
product first run rather than only as an internal integration scenario.

The maintained browser regression at `frontend/tests/browserFirstRun.browser.mjs` extends the
completed #905 onboarding baseline rather than replacing it. It drives the real Vite UI against the
real single-node Control Plane, uses a hermetic local OpenAI-compatible endpoint, proves visible
Task/Plan/Step/Agent/Run/Result/Artifact/Verification evidence, and covers an unusable-provider
failure that must remain actionable and secret-safe without creating canonical Task state. CI runs
this scenario in the existing frontend browser lane and retains bounded screenshot/page/process
evidence only when the browser regression fails.
