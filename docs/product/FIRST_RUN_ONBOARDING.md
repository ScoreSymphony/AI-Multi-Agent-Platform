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
| `needs_model` | No currently routable, text-capable local/self-hosted model is available | configure a model or revalidate its provider health |
| `needs_project` | A model is routable, but the current actor owns no Project | create a Project |
| `needs_workspace` | Exactly one Project is selected implicitly, but it has no compatible owned Workspace | create a Workspace |
| `needs_general_assistant` | The selected scope has no enabled execution-compatible General Assistant | bootstrap/clone #77 starter or repair its editable execution policy |
| `needs_selection` | More than one Project, Workspace or General Assistant could be selected | pass explicit canonical IDs to `onboarding.run-first-task` |
| `ready_for_task` | The ID-less first-Task path is unambiguous and its selected General Assistant passes execution preflight | `onboarding.run-first-task` or the canonical Chat surface |

Readiness deliberately uses canonical effective model health, not only the health value persisted in
the `ModelConfiguration`. The same health states accepted by normal routing (`healthy` and
`degraded`) satisfy first-run readiness; `unknown` and `unavailable` do not. The model must also
provide the `text` modality required by the General Assistant first-Task path.

`ready_for_task` is intentionally stronger than merely finding an enabled starter clone. The
selected editable General Assistant is run through the same side-effect-free `AgentRuntime`
preflight used by first execution. This checks its current model policy, task-override policy,
required capabilities and actual model route. The reference first-run lifecycle additionally
requires inline role-instruction content, so an otherwise valid clone using only an instruction
reference is reported as non-ready rather than failing after a Task has already been created.

The status exposes `general_assistant_blockers` for scoped clones that fail this preflight. The
entries contain canonical Agent/Project/Workspace IDs, the canonical error code and an actionable
message; they do not contain provider credentials or resolved secret material.

When the actor owns multiple selectable resources, onboarding reports `needs_selection` together
with `selection_kind`, `selection_required` and candidate Project/Workspace/Agent IDs. This does not
mean the selected resources are unusable. A caller may provide explicit `project_id`, `workspace_id`
and `agent_id` to `onboarding.run-first-task`; that exact path is resolved and preflighted before any
Task is created.

The status also reports separate counts for `local`, `self_hosted` and `remote` model configurations,
plus text-capable and currently usable golden-path counts. A remote model never satisfies the
local-model golden path merely by existing in the registry. The response also exposes the installed
onboarding adapter IDs and explicitly reports that no remote or paid provider is selected
automatically.

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

For the first-Task readiness path, clone the `general_assistant` starter for the authenticated user,
keep the editable clone enabled and bind it to the selected owned Project and Workspace. The
resulting resource remains an ordinary editable canonical Agent and is not overwritten by starter
upgrades.

Editing remains fully supported, but readiness reflects whether the **current revision** can execute
the reference first-run Task. In particular, the first-run path needs inline role instructions,
allows the Task layer to add the `text`/self-hosted model requirement, and must be able to resolve all
required capabilities and the resulting model route. A change that intentionally makes the Agent
incompatible therefore moves onboarding back to `needs_general_assistant` with a blocker instead of
reporting a false `ready_for_task` state.

## First real Task result

When onboarding reports `ready_for_task`, or when `needs_selection` is resolved by explicit IDs, the
task-based golden path can execute:

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

`project_id`, `workspace_id` and `agent_id` may be omitted only when each corresponding selection is
unambiguous. With explicit IDs, the command resolves ownership/scope and runs the shared execution
preflight on that exact Agent revision **before** creating the canonical Task.

The command then goes through the canonical Task/Run and Agent runtime path, routes through the
selected canonical `ModelConfiguration`, invokes the configured `ModelProvider`, records the
AgentRun, writes the Run output, attaches a canonical Result and returns the visible result
identifiers/output. The Task, Run, AgentRun and Result remain readable after restart.

This Task path is independent of Chat. The conversational entrypoint consumes the same canonical
runtime without #250 introducing a private chat backend.

## CLI

The CLI exposes thin API-only onboarding commands:

```text
platform onboarding status
platform onboarding configure-model ...
platform onboarding run-first-task ...
```

These commands use the same `/api/v1/onboarding/...` and `/api/v1/commands/...` Control Plane
surfaces documented above. CLI code does not import or call `ModelRegistry`, `AgentRuntime` or a
model backend directly. The progressive Web UI can consume the same API surface.

Provider health revalidation uses the existing canonical ModelProvider administration command:

```text
platform model-provider refresh-health <provider-id> --idempotency-key <unique-key>
```

The local authorization vocabulary recognizes this canonical action, so the bootstrapped first
administrator can perform the revalidation without bypassing #15.

## Restart behavior

The single-node composition restores:

- non-secret model-provider adapter setup metadata;
- canonical `ModelConfiguration` inventory;
- provider attachment needed for the configured model route;
- onboarding command replay records;
- canonical Task/Run/Result and Agent repository state through their existing durable stores.

A newly reconstructed runtime provider begins with runtime health `unknown`. Persisted model health
is intentionally not treated as proof that the endpoint is still reachable. Therefore onboarding
returns `needs_model` after restart until provider health is revalidated through the canonical
ModelProvider health command. Once the endpoint reports a routable health state, the existing
Project, Workspace and General Assistant become usable again without recreation. If several such
paths exist, the restored state correctly returns to `needs_selection` rather than inventing an
implicit selection. Regression coverage executes another real model-backed Task using explicit IDs
after revalidation and verifies that it reaches a new canonical Result.

Authentication, authorization, Project, Workspace and the remaining canonical domains continue to
be owned by their existing persistence boundaries. Secret values are subject to the selected
SecretProvider's own durability semantics and are never persisted by onboarding itself.

## Readiness implementation boundary

There is one authoritative `OnboardingService.status()` implementation. Model configuration,
persistence and first-run readiness live in the same service rather than using a base status method
plus a shadowing readiness subclass. The public `ai_multi_agent_platform.onboarding.OnboardingService`
export points directly at that implementation.

Execution compatibility itself is not duplicated there: both readiness and the lifecycle use the
shared side-effect-free first-run Agent preflight, which delegates model/capability policy to
`AgentRuntime.prepare_agent()`.

## Issue #250 boundary

The #250 slice covers:

1. understandable fresh-install/no-model status;
2. provider-neutral local/self-hosted model configuration and validation;
3. execution-compatible effective-health and text-capability readiness;
4. explicit zero-paid-service reference behavior with no remote fallback;
5. canonical #34 `SecretReference` integration without plaintext persistence;
6. reuse of canonical Project/Workspace and an editable #77 General Assistant;
7. side-effect-free execution preflight for the current edited General Assistant revision;
8. explicit selection guidance when multiple Project/Workspace/Agent paths exist;
9. a real Agent-driven first Task producing a canonical visible Result;
10. restart restoration followed by explicit provider-health revalidation and another real Task/Result;
11. API-only CLI onboarding commands using the same Control Plane surface;
12. retry-safe `configure-model` command replay and recursive plaintext credential rejection;
13. one authoritative first-run readiness implementation.

The broader guided Web experience and the reproducible single-node product acceptance gate remain
separate integration/acceptance work. #250 is complete only when the full branch CI, fresh-install
smoke and the execution-preflight/selection/restart regression coverage are green.
