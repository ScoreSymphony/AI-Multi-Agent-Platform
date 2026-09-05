# Guided Web first-run onboarding

Issue #395 adds the browser product journey for the canonical first-run contracts from #250 and the execution-precise selection semantics from #397.

## Architecture boundary

```text
Browser
  -> BrowserSessionClient (cookie + CSRF)
  -> typed frontend clients
  -> /api/v1 Control Plane
  -> canonical onboarding / Project / Workspace / Agent / Model / Task APIs
```

The browser never connects directly to a model server, model gateway, Hermes, Forge, LiteLLM, MCP server, repository or provider-private administration endpoint. The guided page is a projection of canonical Control Plane state; it does not own a second readiness or execution model.

## Route and discovery

The product route is `/onboarding`. It is functional only when API discovery advertises the canonical `onboarding` resource. When the resource is absent, the ordinary manifest-aware unavailable page is rendered.

When `onboarding` is available and `GET /api/v1/onboarding/first-run` reports a state other than `ready_for_task`, the main shell shows a direct callout to the guided route. Failure to read onboarding state does not replace or break the rest of the shell.

The page renders these server-owned states:

- `needs_model`;
- `needs_project`;
- `needs_workspace`;
- `needs_general_assistant`;
- `needs_selection`;
- `ready_for_task`.

Server-provided guidance, execution blockers and candidate IDs remain authoritative.

## Model setup

Model configuration uses only:

```text
POST /api/v1/commands/onboarding.configure-model
```

The form requires an adapter ID already reported by onboarding and supports only the first-run locations `local` and `self_hosted`. There is no remote/paid option and no automatic remote provider selection.

Credential-bearing endpoints accept only canonical SecretReference metadata:

```json
{
  "provider": "local-secrets",
  "secret_id": "model-token",
  "scope": "platform",
  "version": "current"
}
```

There is deliberately no field for an API key, bearer token, password or secret value. Resolved secret material remains behind the server-side #34 SecretProvider boundary.

After a process restart, persisted model configuration can exist while runtime provider health is `unknown`. The page keeps the canonical `needs_model` state and can invoke the existing ModelProvider health command through the normal Control Plane client. It does not recreate the ModelConfiguration or treat endpoint reachability as proof outside #250's server-side revalidation rules.

## Project and Workspace

The guided page creates Projects and Workspaces through the same existing `ControlPlaneClient` methods used elsewhere in the frontend:

- `POST /api/v1/projects`;
- `POST /api/v1/workspaces`.

Project ownership is derived from the authenticated browser actor returned by `/api/v1/auth/me`; the page does not ask the user to type another owner identity. The first Workspace uses the canonical persistent-project/read-write profile and the Project ID returned/provided by first-run state.

## General Assistant

The guided page does not create a frontend-only Assistant. It uses the existing #77 starter lifecycle:

```text
POST /api/v1/commands/standard-agent.bootstrap
POST /api/v1/commands/standard-agent.clone
```

Bootstrap remains explicit. The clone uses `resource_ref=general_assistant` and the selected canonical Project/Workspace scope. The resulting Agent remains an ordinary editable user-owned Agent and is visible in the normal Agents product surface.

Existing General Assistant preflight blockers are rendered as server-provided execution blockers rather than being reinterpreted in the browser.

## Selection and first Task

When #397 reports `needs_selection`, the browser displays the returned canonical Project, Workspace and Agent candidate IDs. It does not choose among multiple candidates automatically. Selected IDs are passed to:

```text
POST /api/v1/commands/onboarding.run-first-task
```

The server still resolves ownership/scope and performs the exact shared first-run execution preflight before creating a Task. The browser never treats a selected dropdown value as authorization or proof of executability.

A successful command returns the canonical Task, Run and Result identifiers plus Run output. The page displays these identifiers and links to the existing Task, Run and Result detail routes instead of creating a separate first-run result store.

## BrowserSession, CSRF and idempotency

`OnboardingClient` is constructed with `BrowserSessionClient.fetch`, matching the rest of the shell. Unsafe cookie-authenticated requests therefore receive the current `X-CSRF-Token` from the shared session boundary.

Every onboarding mutation generates a fresh `Idempotency-Key` and correlation ID. The browser sends `credentials: include` and preserves canonical Control Plane errors. There is no retry path that bypasses the Control Plane.

## Manifest and degraded behavior

The `onboarding` resource gates the route itself. Individual actions also respect advertised command/resource availability:

- model setup: `onboarding.configure-model`;
- Project/Workspace creation: `projects` / `workspaces`;
- starter bootstrap/clone: `standard-agent.bootstrap` / `standard-agent.clone`;
- first Task: `onboarding.run-first-task`;
- restart health recovery: `model-providers` resource and the existing provider refresh command.

If one of these surfaces is absent, the journey renders an explicit unavailable/degraded action instead of calling a private backend.

## Regression coverage

Focused frontend tests verify:

- first-run state mapping for all canonical #250 states;
- canonical `/api/v1/onboarding/first-run` reads;
- `onboarding.configure-model`, starter and first-Task command URLs;
- BrowserSession CSRF propagation and idempotency headers;
- SecretReference-only credential configuration;
- execution-precise explicit selection payloads;
- no guessing among multiple executable candidates;
- unique-path first-Task payload construction;
- restart `needs_model` representation with persisted local model inventory;
- manifest gating for the `onboarding` resource;
- absence of direct model-backend/Hermes/Forge/LiteLLM URLs in the onboarding client tests.
