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
