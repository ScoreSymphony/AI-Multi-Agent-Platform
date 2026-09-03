# Progressive administrative CLI

Issue: #38

This document extends `docs/CLI.md` with the canonical administrative surface that became available after the initial CLI foundation.

## Architecture invariant

All commands in this document remain northbound clients of the versioned Control Plane:

```text
platform CLI -> /api/v1 Control Plane -> canonical application services
```

They do not access the Model Registry, kernel repositories, provider SDKs, databases, Hermes, Forge, MCP, workers, or other replaceable implementations directly.

## Model providers

The current Control Plane exposes the canonical Model Registry provider surface implemented by the model domain:

```bash
platform model-provider list
platform model-provider show PROVIDER_ID
platform model-provider refresh-health PROVIDER_ID
platform --yes model-provider enable PROVIDER_ID
platform --yes model-provider disable PROVIDER_ID
```

`enable` and `disable` are meaningful mutations and therefore require explicit confirmation. In non-interactive/headless operation, pass the global `--yes` flag. An explicit `--idempotency-key` may also be supplied to mutation commands; otherwise the CLI generates one for the canonical API request.

`refresh-health` refreshes canonical health state but does not enable or disable a provider, so it does not require `--yes`.

The CLI never prints model-provider credentials or provider-private request/response payloads.

## Model configurations

```bash
platform model list
platform model show MODEL_ID_OR_ALIAS
platform --yes model enable MODEL_ID_OR_ALIAS
platform --yes model disable MODEL_ID_OR_ALIAS
```

Model enable/disable operations use the same confirmation and idempotency rules as provider enable/disable operations.

## Task reference inspection

The current Control Plane already exposes canonical references attached to Task state. The CLI now exposes them directly:

```bash
platform plan list
platform plan show PLAN_ID

platform step list
platform step show STEP_ID

platform artifact list
platform artifact show ARTIFACT_ID

platform result list
platform result show RESULT_ID
```

These are read-only inspection commands. List commands support the same pagination, filtering, search, field-selection, human-output, and JSON-output conventions as the rest of the CLI.

## Task planning management

The composed Control Plane exposes the built-in #88 Task-management commands separately from generic extension commands. The CLI exposes those native commands explicitly:

```bash
platform --yes task update-management TASK_ID \
  --changes-json '{"priority":"urgent","labels":["release"]}'

platform --yes task bulk-update-management \
  --updates-json '[{"task_id":"TASK_ID","changes":{"archived":true}}]'
```

Both operations are meaningful canonical Task mutations and therefore require confirmation. Headless usage must pass the global `--yes` flag. Both accept `--idempotency-key`; otherwise the CLI generates one for the canonical request.

The single-update payload must be a non-empty JSON object. The CLI reserves `resource_ref` so the positional `TASK_ID` remains authoritative. The bulk payload must be a non-empty JSON array and is limited to the canonical maximum of 100 update entries.

The CLI deliberately does not duplicate #88 field, dependency, cross-project, assignment or eligibility validation. Those rules remain authoritative in the Task-management application layer behind the Control Plane. The CLI only validates the local JSON shape before sending:

```text
platform task update-management
    -> POST /api/v1/commands/task-management.update
    -> TaskManagementService
    -> canonical Task state/event history
```

Bulk updates use `/api/v1/commands/task-management.bulk-update`. The server preflights authorization for every targeted Task before applying mutations and currently reports `atomic=false`; the CLI surfaces that canonical result unchanged.

## Output redaction

All CLI rendering now applies the reusable platform redaction policy from #34 before data reaches stdout or stderr. This is a defense-in-depth layer in addition to the Control Plane requirement that ordinary API responses never expose plaintext secrets.

Redaction applies recursively to:

- successful JSON responses;
- successful human-readable output;
- locally rendered profile/configuration metadata;
- canonical error details.

Sensitive fields such as passwords, API keys, tokens, credentials, client secrets, private keys and matching suffixed keys render as `[REDACTED]`. Non-sensitive metadata and secret references remain visible so operators can diagnose configuration without resolving secret material.

This CLI-side protection does not make secret-bearing backend responses acceptable. Canonical APIs remain responsible for returning only safe/redacted data in the first place.

## Doctor health semantics

`platform doctor` consumes the canonical Control Plane manifest, `/health`, and `/readiness` endpoints. It does not probe providers or adapters directly.

The current provider health vocabulary is:

- `healthy` — provider is available and healthy;
- `degraded` — provider remains available but reports degraded health;
- `unknown` — provider remains available but its health is not established;
- `unavailable` — provider is unavailable and canonical readiness fails.

Doctor classifies the aggregate result as:

- `healthy` with exit code `0` when the Control Plane is reachable, API-compatible, ready, and all reported providers are healthy;
- `degraded` with exit code `1` when canonical readiness remains true but at least one provider reports `degraded` or `unknown`;
- `blocking` with exit code `4` when the Control Plane is unreachable, readiness fails, a provider is unavailable/not available, the API version is incompatible, or the canonical health payload is structurally invalid.

`/readiness` remains authoritative for whether the composed Control Plane can accept work. Provider checks add diagnostic detail; the CLI does not invent its own required-versus-optional dependency topology.

## Cancellation safety

Task and Run cancellation are also meaningful side effects. They now use the same confirmation boundary:

```bash
platform --yes task cancel TASK_ID
platform --yes run cancel RUN_ID --task-id TASK_ID
```

When `--yes` is omitted in a non-interactive environment, the CLI refuses the mutation before sending any Control Plane request. In an interactive terminal it asks for confirmation.

This confirmation layer does not grant permission. The Control Plane remains authoritative for authentication, authorization, approvals, idempotency, and the actual state transition.

## Still progressive

Issue #38 remains open. Commands for agents/teams, capabilities/tools, nodes/workers, approvals, authentication, safe configuration/secrets, automations, evaluations, plugins, import/export, and other later domains must be added only when their canonical Control Plane APIs are present. The CLI must not invent direct backend shortcuts while waiting for those APIs.
