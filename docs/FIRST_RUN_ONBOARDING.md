# First-run onboarding and local-model golden path

This document describes the issue #250 onboarding composition. The onboarding layer is a thin
orchestration surface over existing canonical platform domains; it does not replace Project,
Workspace, Agent, Model or Task/Run lifecycle APIs.

## Design constraints

The first-run path follows these invariants:

- the supported runtime is the ordinary self-hosted single-node profile from #39;
- authentication and authorization remain the #36/#15 boundaries;
- Projects and Workspaces remain canonical #32/#37 resources;
- models remain #10 `ModelConfiguration` resources behind `ModelProvider` adapters;
- starter Agents remain ordinary, editable #77 `AgentDefinition` resources;
- no provider, model vendor or paid service is selected silently;
- no prompt is transmitted to a remote endpoint merely because onboarding is opened;
- plaintext credentials are not accepted by onboarding configuration;
- credential-bearing endpoints use canonical #34 `SecretReference` objects and resolve secret
  material only at the concrete adapter boundary;
- provider-native model names remain adapter metadata rather than canonical model identity;
- core onboarding code depends on the provider-neutral `OnboardingModelAdapter` protocol; concrete
  OpenAI-compatible composition lives under `adapters`, outside canonical contracts.

The shipped `platform-server` distribution installs the OpenAI-compatible onboarding bridge at its
outer composition boundary. Installing that bridge does **not** choose a model, endpoint, API
account, remote service or paid provider.

## First-run status

After authenticating, read:

```text
GET /api/v1/onboarding/first-run
```

The returned `onboarding_status.state` is one of:

| State | Meaning | Next canonical action |
|---|---|---|
| `needs_model` | No usable local/self-hosted model is explicitly configured | `onboarding.configure-model` |
| `needs_project` | Model is ready, but the current actor owns no Project | create/select a Project |
| `needs_workspace` | Project exists, but no owned Workspace exists | create/select a Workspace |
| `needs_general_assistant` | Model + scope exist, but no owned General Assistant clone exists | bootstrap/clone #77 starter |
| `ready_for_task` | first-run prerequisites are present | `onboarding.run-first-task`, or Chat when #72 is present |

The status also reports separate counts for `local`, `self_hosted` and `remote` model
configurations. A remote model never satisfies the local-model golden path merely by existing in
the registry. The response also exposes the installed onboarding adapter IDs and explicitly reports
that no remote or paid provider is selected automatically.

## Configure a local/self-hosted model

The shipped distribution currently includes an OpenAI-compatible onboarding bridge. This remains an
adapter choice rather than a canonical model-contract choice; another installed
`OnboardingModelAdapter` can implement the same first-run contract without changing the Control
Plane API.

Call the generic extension command:

```text
POST /api/v1/commands/onboarding.configure-model
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "resource_ref": "first-run",
  "adapter_id": "openai-compatible",
  "provider_id": "local-openai",
  "model_config_id": "model-qwen-local",
  "provider_model": "qwen-local",
  "display_name": "Qwen Local",
  "base_url": "http://127.0.0.1:8001/v1",
  "location": "local",
  "capabilities": {
    "context_window": 32768,
    "tool_calling": true,
    "structured_output": true,
    "streaming": false,
    "modalities": ["text"]
  }
}
```

Before persisting anything, onboarding checks provider health and verifies that `provider_model` is
reported by the adapter's native model inventory. For the OpenAI-compatible bridge this validation
uses its `/models` route.

### Location semantics

- `local`: the endpoint must be loopback (`localhost`, `127.0.0.0/8`, `::1`, etc.).
- `self_hosted`: the operator explicitly points at a self-managed endpoint which may be on another
  machine.
- `remote`: deliberately rejected by this first-run command. Remote/commercial providers can be
  configured through normal model administration, but are not part of the zero-recurring-cost
  golden path and are never selected implicitly.

## Secrets

Credential-free endpoints require no secret configuration. Credential-bearing endpoints pass a
canonical #34 reference rather than secret material:

```json
{
  "credential_ref": {
    "provider": "local-secrets",
    "secret_id": "local-model-token",
    "scope": "platform"
  }
}
```

The onboarding layer persists only this value-free `SecretReference`. The concrete model adapter
resolves the reference through the installed `SecretProvider` immediately before an outbound
request and injects the resulting short-lived material into that request. The secret value is not
copied into `ModelConfiguration`, provider setup JSON, command replay JSON or Control Plane
responses.

The shipped minimal single-node composition currently uses #34 `LocalSecretProvider`, whose secret
material is intentionally memory-only. Its reference metadata survives in onboarding configuration,
but the secret value itself must be reprovisioned after process restart. A durable SecretProvider can
replace this backend without changing the canonical references or onboarding contract.

Common plaintext credential keys are rejected recursively, including when nested inside objects or
arrays. URLs containing embedded credentials are also rejected.

## Idempotency and retries

`onboarding.configure-model` requires the ordinary Control Plane idempotency key. The single-node
profile persists a value-free replay record containing the principal, key, command, resource,
payload digest and safe result.

- repeating the same key with the same payload returns the persisted result without calling the
  provider again;
- repeating the same key with a different payload fails with `CONFLICT` before any provider call;
- an identical configuration does not increment the canonical model revision merely because setup
  was retried;
- command replay persistence contains no resolved secret material.

This replay state is restored across single-node process restart.

## General Assistant

Onboarding does not invent a second Agent lifecycle. Use the existing #77 commands:

```text
POST /api/v1/commands/standard-agent.bootstrap
POST /api/v1/commands/standard-agent.clone
```

Clone the `general_assistant` starter for the authenticated user and, where useful, bind the clone
to the selected Project/Workspace. The resulting resource is an ordinary editable canonical Agent
and is not overwritten by starter upgrades.

## First real Task result

When onboarding reports `ready_for_task`, the task-based golden path can execute:

```text
POST /api/v1/commands/onboarding.run-first-task
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "resource_ref": "first-run",
  "objective": "Return one short local response.",
  "project_id": "<project-id>",
  "workspace_id": "<workspace-id>",
  "agent_id": "<general-assistant-id>"
}
```

The command goes through the canonical Task/Run and Agent runtime path, routes through the selected
canonical `ModelConfiguration`, invokes the configured `ModelProvider`, records the AgentRun, writes
the Run output, attaches a canonical Result and returns the visible result identifiers/output. The
Task, Run, AgentRun and Result remain readable after restart.

This Task path is independent of #72. The conversational entrypoint can later consume the same
canonical runtime without #250 introducing a private chat backend.

## CLI

The CLI now exposes thin API-only onboarding commands:

```text
platform onboarding status
platform onboarding configure-model ...
platform onboarding run-first-task ...
```

These commands use the same `/api/v1/onboarding/...` and `/api/v1/commands/...` Control Plane
surfaces documented above. CLI code does not import or call `ModelRegistry`, `AgentRuntime` or a
model backend directly. The progressive Web UI in #236 can consume the same API surface.

## Restart behavior

The single-node composition restores:

- non-secret model-provider adapter setup metadata;
- canonical `ModelConfiguration` inventory;
- provider attachment needed for the configured model route;
- onboarding command replay records;
- canonical Task/Run/Result and Agent repository state through their existing durable stores.

Authentication, authorization, Project, Workspace and the remaining canonical domains continue to
be owned by their existing #39 persistence boundaries. Secret values are subject to the selected
SecretProvider's own durability semantics and are never persisted by onboarding itself.

## Issue #250 boundary

The implemented #250 slice now covers:

1. understandable fresh-install/no-model status;
2. provider-neutral local/self-hosted model configuration and validation;
3. explicit zero-paid-service reference behavior with no remote fallback;
4. canonical #34 `SecretReference` integration without plaintext persistence;
5. reuse of canonical Project/Workspace and editable #77 General Assistant resources;
6. a real Agent-driven first Task producing a canonical visible Result;
7. restart restoration of model/provider configuration and canonical Task/Run/Result state;
8. API-only CLI onboarding commands using the same Control Plane surface intended for #236;
9. retry-safe `configure-model` command replay and recursive plaintext credential rejection.

Before #250 is closed, the complete branch CI and fresh-install lifecycle smoke must be green and
the acceptance coverage must be reviewed against the issue one final time. The progressive Web UI
itself remains #236, and the Chat entrypoint remains #72.
