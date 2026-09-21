# Canonical CLI

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

## Supported scope

The maintained CLI provides the following API-first baseline:

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

The CLI does not invent commands for domains without canonical APIs. Additional product domains extend the same API-first client boundary.

## Installation and entry point

After installing the package, run:

```bash
platform --help
platform status
platform health
platform version
platform doctor
```

`platform --help` is the authoritative root discovery surface for the complete supported product CLI. It is generated from the same composed command tree used by shell completion.

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
platform task list --filter status=draft
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

## Evaluation and regression commands

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

The package also installs a dependency-free `platform-completion` helper. Root help and completion use the same composed `argparse` product tree as the installed `platform` entry point. The composed tree reuses the parser-registration functions owned by each CLI layer rather than maintaining a second command registry, so wrapper-owned domains such as `auth`, `approval`, `repository`, `registry`, `learning`, and `trace` remain discoverable alongside the core commands. Nested help continues to resolve through the parser owned by the selected domain.

The completion helper only introspects that parser tree; it does not read CLI profiles, contact the Control Plane, or resolve secrets.

Enable completion for the current shell session with one of:

```bash
eval "$(platform-completion bash)"
eval "$(platform-completion zsh)"
platform-completion fish | source
```

The helper completes the current command hierarchy, command options, and finite option choices such as `--direction` and `--owner-type`. Dynamic resource IDs are intentionally not fetched during tab completion, so completion cannot cause network access or administrative side effects.

## Doctor contract

`platform doctor` is a northbound diagnostic client over canonical Control Plane surfaces. It validates:

- CLI configuration;
- `/api/v1` reachability and API-major compatibility;
- the canonical manifest, health and readiness surfaces;
- canonical dependency/provider health reported by the Control Plane;
- Node and Worker health through Control Plane resources when the distributed compute surface is registered.

The diagnostic vocabulary is `healthy`, `degraded`, and `blocking`. Optional compute resources may degrade diagnostics without redefining Control Plane readiness. The CLI does not probe providers, workers or backend-private services directly.

## Verification

CLI changes are covered by the repository's normal quality gates (`ruff format --check`, `ruff check`, strict `mypy`, `pytest`, and package build). Integration/contract tests exercise HTTP-style transports so the CLI remains on the real versioned Control Plane boundary rather than direct kernel or repository access.

## Extension model

Additional domain commands extend the same API-first client boundary through their canonical Control Plane contracts. Evaluation and other product domains must not introduce CLI-owned lifecycle authority or backend-private shortcuts.