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

The foundation intentionally did not invent commands for domains before their canonical APIs existed. Progressive integrations now add those surfaces while preserving the same API-first client boundary.

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

Profiles are deliberately non-secret. Accepted fields are only endpoint, principal reference, owner type, and owner ID. Endpoint URLs containing username/password credentials are rejected, and unknown profile fields are rejected. Authentication credentials/tokens belong to the authentication integration and its approved credential storage path, not this file.

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

## Evaluation and regression commands (#19)

Evaluation commands are a thin northbound adapter over the canonical Evaluation Control Plane resources and commands. The CLI never constructs an `EvaluationRunner`, reads the evaluation repository directly, aggregates repetition samples locally, or introduces a second evaluation lifecycle.

Configured suites are addressed by exact versioned references:

```bash
platform eval suite list
platform eval suite show suite_id@version
```

Execute a suite with an explicit immutable `ConfigurationSnapshot`:

```bash
platform eval run suite_id@version \
  --snapshot-json '{"platform_version":"0.0.1","platform_commit":"abc123","references":[],"environment":[]}' \
  --seed 41 \
  --idempotency-key eval-run-001
```

`--snapshot-json` must be a JSON object with a non-blank `platform_version`. The Control Plane remains authoritative for the complete snapshot schema, including canonical version references and environment values. `--repetitions` defaults to `1`. Repeated runs persist every raw repetition result; any reduction of those samples into comparable values is owned by the Evaluation service under an exact versioned aggregation policy.

Optional run arguments are:

- `--baseline-run-id`;
- `--regression-policy-ref` using an exact versioned regression-policy reference;
- `--aggregation-policy-ref` using an exact versioned aggregation-policy reference; required when an automatic baseline comparison involves repeated samples;
- `--repetitions`;
- `--seed`;
- `--idempotency-key`.

For example, an automatic repeated baseline comparison can be requested with:

```bash
platform eval run suite_id@version \
  --snapshot-json '{"platform_version":"0.0.1","platform_commit":"abc123","references":[],"environment":[]}' \
  --repetitions 5 \
  --seed 41 \
  --baseline-run-id evaluation_run_baseline \
  --regression-policy-ref policy_id@version \
  --aggregation-policy-ref aggregation_id@version \
  --idempotency-key eval-run-repeated-001
```

Inspect the durable run detail, including raw evaluator results, any stored derived aggregates, and any stored comparison:

```bash
platform eval result show evaluation_run_...
```

Persist a comparison for completed runs:

```bash
platform eval compare evaluation_run_current \
  --baseline-run-id evaluation_run_baseline \
  --regression-policy-ref policy_id@version \
  --aggregation-policy-ref aggregation_id@version \
  --idempotency-key eval-compare-001
```

`--aggregation-policy-ref` may be omitted when both runs are single-repetition runs and raw `EvaluationResult` records are compared directly. When either run contains repeated samples, the exact aggregation-policy reference is required; the CLI only forwards that reference and never chooses or executes an aggregation method itself.

Both mutations call `/api/v1/commands/evaluation.*`; reads use `/api/v1/evaluation-suites` and `/api/v1/evaluation-runs`.

## Shell completion

The package also installs a dependency-free `platform-completion` helper. It introspects the same `argparse` command tree as `platform`; it does not read CLI profiles, contact the Control Plane, or resolve secrets.

Enable completion for the current shell session with one of:

```bash
eval "$(platform-completion bash)"
eval "$(platform-completion zsh)"
platform-completion fish | source
```

The helper completes the current command hierarchy, command options, and finite option choices such as `--direction` and `--owner-type`. Dynamic resource IDs are intentionally not fetched during tab completion, so completion cannot cause network access or administrative side effects.

## Initial doctor contract

`platform doctor` currently validates the foundation that exists today:

- CLI configuration parsed successfully;
- `/api/v1` is reachable;
- API major is compatible (`v1`);
- canonical health endpoint responds;
- canonical readiness endpoint responds.

The diagnostic vocabulary is `healthy`, `degraded`, and `blocking`. Provider/worker/auth/secret/permission checks are added only after the corresponding canonical platform domains exist.

## Verification

CLI changes are covered by the repository's normal quality gates (`ruff format --check`, `ruff check`, strict `mypy`, `pytest`, and package build). Integration/contract tests exercise HTTP-style transports so the CLI remains on the real versioned Control Plane boundary rather than direct kernel or repository access.

## Progressive #38 work still open

This document does not close #38. Later work should continue extending the same client when owning canonical APIs require additional CLI surfaces. Evaluation is now integrated through #19 and must remain API-first.