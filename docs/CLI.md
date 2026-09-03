# Canonical CLI

Issue: #38

The `platform` command is a northbound client of the versioned Control Plane. It must never use kernel repositories, databases, Hermes, Forge, MCP servers, model-provider SDKs, workers, or other backend-private interfaces directly.

```text
platform CLI
    |
    v
/api/v1 Control Plane
    |
    v
canonical application services
```

## Foundation scope

This first #38 slice provides the CLI foundation that can be implemented solely on top of #32:

- installable `platform` entry point;
- local and remote non-secret target profiles;
- endpoint/profile/environment overrides;
- request timeout and safe GET retry behavior;
- request/correlation ID propagation;
- canonical API error rendering;
- human-readable and stable JSON output;
- `status`, `health`, `version`, and initial `doctor` diagnostics;
- project and workspace create/list/show commands supported by the current Control Plane;
- task create/list/show/queue/start/cancel/retry/timeline commands;
- run list/show/cancel commands.

The foundation intentionally does not invent commands for Agents, Approvals, Nodes, Workers, Automations, Plugins, Search, or other domains before their canonical APIs exist. Those are progressive #38 integrations.

## Installation and entry point

After installing the package, run:

```bash
platform --help
platform status
platform health
platform version
platform doctor
```

The CLI client version is also available without contacting a Control Plane:

```bash
platform --client-version
```

## Profiles and endpoint resolution

The default profile is `local` and targets:

```text
http://127.0.0.1:8000
```

The default configuration file is `$XDG_CONFIG_HOME/ai-multi-agent-platform/cli.json`, or `~/.config/ai-multi-agent-platform/cli.json` when `XDG_CONFIG_HOME` is unset.

Overrides, from highest to lowest priority:

1. `--endpoint` / `--profile`;
2. `AI_PLATFORM_ENDPOINT` / `AI_PLATFORM_PROFILE`;
3. the selected saved profile.

`AI_PLATFORM_CONFIG` changes the default configuration path. `--config` has highest priority for the path itself.

Example:

```bash
platform profile set remote https://control.example.net \
  --principal-ref user:operator \
  --owner-type user \
  --owner-id operator
platform profile use remote
platform status
```

Profiles are deliberately non-secret. Accepted fields are only endpoint, principal reference, owner type, and owner ID. Endpoint URLs containing username/password credentials are rejected, and unknown profile fields are rejected. Authentication credentials/tokens belong to the future #36 authentication integration and its approved credential storage path, not this file.

## Output contract

Human output is the default. Collections are rendered as compact tables when possible.

Use `--json` for scripting:

```bash
platform --json task list
```

Successful remote responses use:

```json
{
  "data": {},
  "meta": {
    "api_version": "v1",
    "correlation_id": "corr_...",
    "request_id": "request_...",
    "status": 200
  }
}
```

The `data` member contains canonical Control Plane data without backend-private reinterpretation. Request and correlation IDs are preserved for support and audit workflows.

Canonical API failures preserve the API error code/category/message and IDs. Local profile and transport failures use stable CLI-specific error codes without pretending to be server responses.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | command succeeded / doctor healthy |
| `1` | doctor completed with degraded findings |
| `2` | local CLI/profile/configuration error |
| `3` | canonical Control Plane API error |
| `4` | Control Plane transport failure or blocking doctor result |

Argument syntax errors are handled by `argparse` and use its standard non-zero usage exit.

## Retry and mutation safety

Automatic retries are limited to GET requests and transient HTTP `502`, `503`, and `504` responses or transport failures. Mutating POST requests are never automatically replayed by the CLI, even though the CLI always supplies an `Idempotency-Key`. This avoids hiding ambiguous mutation outcomes behind client retries.

Operators can explicitly repeat a mutation with the same `--idempotency-key` when they need canonical replay semantics.

## Core examples

```bash
platform project create --name Demo --owner-type user --owner-id operator
platform project list
platform workspace create --project-id project_...

platform task create \
  --title "Inspect repository" \
  --objective "Produce a canonical inspection result" \
  --owner-type user \
  --owner-id operator
platform task list --filter status=created
platform task show task_...
platform task queue task_...
platform task start task_...
platform task timeline task_...
platform task cancel task_...
platform task retry task_...

platform run list
platform run list --task-id task_...
platform run show run_...
platform run cancel run_... --task-id task_...
```

List commands support the Control Plane conventions `--limit`, `--cursor`, `--sort`, `--direction`, `--q`, repeatable `--filter FIELD=VALUE`, and `--fields`.

## Initial doctor contract

`platform doctor` currently validates the foundation that exists today:

- CLI configuration parsed successfully;
- `/api/v1` is reachable;
- API major is compatible (`v1`);
- canonical health endpoint responds;
- canonical readiness endpoint responds.

The diagnostic vocabulary is `healthy`, `degraded`, and `blocking`. Provider/worker/auth/secret/permission checks are added only after the corresponding canonical platform domains exist.

## Verification

CLI changes are covered by the repository's normal quality gates (`ruff format --check`, `ruff check`, strict `mypy`, `pytest`, and package build). The integration tests use an in-process HTTP transport so the exercised path is still the real versioned Control Plane boundary rather than direct kernel or repository access.

## Progressive #38 work still open

This slice does not close #38. Later work should extend the same client with canonical APIs from the owning issues, including Agents/Teams, Models/Tools, Nodes/Workers, approvals, observability logs/metrics, automation, plugins, evaluation, search, deployment/update operations, and secure authentication/session handling.
