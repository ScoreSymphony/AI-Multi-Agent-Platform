# Guided Web first-run onboarding

Issue #395 added the browser product journey for the canonical setup contracts from #250 and the
execution-precise single-Agent selection semantics from #397. Issue #894 now makes the #889
multi-agent golden path the **official product first run** once model, Project and Workspace setup is
complete. The General Assistant flow remains available as a compatibility/focused-testing path; it is
no longer a prerequisite for proving the multi-agent product works. See
[`MULTI_AGENT_FIRST_RUN.md`](MULTI_AGENT_FIRST_RUN.md) for the product-level acceptance contract.

## Architecture boundary

```text
Browser
  -> BrowserSessionClient (cookie + CSRF)
  -> typed frontend clients
  -> /api/v1 Control Plane
  -> canonical onboarding / Project / Workspace / Agent / Model / Task APIs
```

The browser never connects directly to a model server, model gateway, Hermes, Forge, LiteLLM, MCP
server, repository or provider-private administration endpoint. The guided page is a projection of
canonical Control Plane state; it does not own a second readiness or execution model.

## Route and discovery

The product route is `/onboarding`. It is functional only when API discovery advertises the canonical
`onboarding` resource. When the resource is absent, the ordinary manifest-aware unavailable page is
rendered.

The page continues to render the server-owned #250 setup/compatibility states:

- `needs_model`;
- `needs_project`;
- `needs_workspace`;
- `needs_general_assistant`;
- `needs_selection`;
- `ready_for_task`.

For #894, `needs_general_assistant`, Agent-level `needs_selection`, and `ready_for_task` no longer
determine whether the official multi-agent card is shown. As soon as a usable local/self-hosted model
and an owned Project/Workspace scope exist, the browser can submit the official multi-agent goal.
General-Assistant-specific states remain authoritative only for the optional
`onboarding.run-first-task` compatibility path.

Server-provided guidance, execution blockers and candidate IDs remain authoritative. When several
owned Projects or Workspaces exist, the browser exposes those canonical candidates instead of
guessing a scope.

## Model setup

Model configuration uses only:

```text
POST /api/v1/commands/onboarding.configure-model
```

The form requires an adapter ID already reported by onboarding and supports only the first-run
locations `local` and `self_hosted`. There is no remote/paid option and no automatic remote provider
selection.

Credential-bearing endpoints accept only canonical SecretReference metadata:

```json
{
  "provider": "local-secrets",
  "secret_id": "model-token",
  "scope": "platform",
  "version": "current"
}
```

There is deliberately no field for an API key, bearer token, password or secret value. Resolved
secret material remains behind the server-side #34 SecretProvider boundary.

After a process restart, persisted model configuration can exist while runtime provider health is
`unknown`. The page keeps the canonical `needs_model` state and can invoke the existing ModelProvider
health command through the normal Control Plane client. It does not recreate the ModelConfiguration
or treat endpoint reachability as proof outside #250's server-side revalidation rules.

## Project and Workspace

The guided page creates Projects and Workspaces through the same existing `ControlPlaneClient`
methods used elsewhere in the frontend:

- `POST /api/v1/projects`;
- `POST /api/v1/workspaces`.

Project ownership is derived from the authenticated browser actor returned by `/api/v1/auth/me`; the
page does not ask the user to type another owner identity. The first Workspace uses the canonical
persistent-project/read-write profile and the Project ID returned/provided by first-run state.

## Official multi-agent goal

Once the model and Project/Workspace prerequisites exist, the primary action is:

```text
POST /api/v1/commands/onboarding.run-multi-agent-golden-path
```

The page submits only the goal plus explicit Project/Workspace IDs when selection is ambiguous. The
server creates or reuses its scoped `researcher`, `developer`, and `reviewer` Agent revisions and
runs the existing #889 planner/runtime. The browser does not construct a private DAG or invoke Agent
runtime internals directly.

A successful response exposes the canonical Task and Plan IDs, every Step and dependency edge, exact
Agent revision assignments, Runs, Results, Artifact IDs, reviewer state, available canonical
Verification records, and trace IDs. The page links to the normal Task/Run/Result detail routes and
does not create a separate first-run result store.

## Optional General Assistant

The guided page does not create a frontend-only Assistant. When the user wants the legacy/focused
single-Agent path, it uses the existing #77 starter lifecycle:

```text
POST /api/v1/commands/standard-agent.bootstrap
POST /api/v1/commands/standard-agent.clone
```

Bootstrap remains explicit. For cloning, the user selects a canonical Workspace candidate and the
browser reads that Workspace through the Control Plane, then derives its canonical `project_id`. It
never lets independent Project and Workspace dropdowns manufacture a mismatched scope pair. The
clone uses `resource_ref=general_assistant` plus that bound Project/Workspace scope. The resulting
Agent remains an ordinary editable user-owned Agent and is visible in the normal Agents product
surface.

Existing General Assistant preflight blockers are rendered as server-provided execution blockers
rather than being reinterpreted in the browser.

## Optional single-Agent first Task

When #397 reports `needs_selection`, the returned candidate IDs remain authoritative for the
compatibility path. The selected Agent identity is passed to:

```text
POST /api/v1/commands/onboarding.run-first-task
```

The shared server resolver validates its canonical Project/Workspace binding, ownership and execution
preflight before creating a Task. This path remains useful for focused General Assistant testing, but
it is not the #894 product first-run acceptance path.

## BrowserSession, CSRF and idempotency

`OnboardingClient` is constructed with `BrowserSessionClient.fetch`, matching the rest of the shell.
Unsafe cookie-authenticated requests therefore receive the current `X-CSRF-Token` from the shared
session boundary.

Every onboarding mutation generates a fresh `Idempotency-Key` and correlation ID. The browser sends
`credentials: include` and preserves canonical Control Plane errors. There is no retry path that
bypasses the Control Plane.

## Manifest and degraded behavior

The `onboarding` resource gates the route itself. Individual actions also respect advertised
command/resource availability:

- model setup: `onboarding.configure-model`;
- Project/Workspace creation: `projects` / `workspaces`;
- official multi-agent first run: `onboarding.run-multi-agent-golden-path`;
- optional starter bootstrap/clone: `standard-agent.bootstrap` / `standard-agent.clone`;
- optional single-Agent Task: `onboarding.run-first-task`;
- restart health recovery: `model-providers` resource and the existing provider refresh command.

If one of these surfaces is absent, the journey renders an explicit unavailable/degraded action
instead of calling a private backend.

## Regression coverage

Focused frontend and product acceptance coverage verifies:

- canonical first-run setup state rendering and fresh-install `needs_model` behavior;
- canonical `/api/v1/onboarding/first-run` reads;
- shared `onboarding.run-multi-agent-golden-path` command usage rather than a browser-only runtime;
- visible Plan/Step dependency, Agent, Run, Result, Artifact, review and trace state;
- BrowserSession CSRF propagation and idempotency headers;
- SecretReference-only credential configuration;
- Project/Workspace scope selection without manufacturing mixed scope pairs;
- the optional General Assistant clone and single-Agent compatibility path;
- restart `needs_model` representation with persisted local model inventory;
- manifest gating for the `onboarding` resource;
- absence of direct model-backend/Hermes/Forge/LiteLLM URLs in the onboarding client;
- the server-side #894 E2E fixture proving the real #889 runtime with no optional orchestration,
  execution, model-gateway, standard-Agent-catalog or paid-provider dependency.
