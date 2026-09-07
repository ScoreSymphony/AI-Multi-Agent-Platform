# CLI extension inspection

Issue: #38

The Control Plane can expose later platform domains through explicitly registered extension collections and commands. The CLI discovers that surface from the canonical OpenAPI document instead of hard-coding a backend implementation.

## Read-only discovery

```bash
platform extension collections
platform extension commands
```

These commands read `/api/v1/openapi.json` and use only:

- `x-registered-extension-collections`;
- `x-registered-extension-commands`.

This makes the current composed API visible without assuming that a completed domain is automatically mounted into every deployment.

## Registered resource inspection

```bash
platform extension list COLLECTION
platform extension show COLLECTION RESOURCE_ID
```

Before accessing the collection, the CLI verifies that it is present in `x-registered-extension-collections`. It then performs an ordinary canonical GET through `/api/v1/<collection>` or `/api/v1/<collection>/<resource-id>`.

Pagination, filtering, field selection, human output, JSON output, correlation metadata and defense-in-depth redaction use the same contracts as other CLI reads.

## No generic command execution

The CLI deliberately does **not** provide a generic equivalent of:

```text
POST /api/v1/commands/{command}
```

Registered extension commands may have destructive, privileged or externally visible side effects. A generic executor would not know the owning domain's confirmation, approval, dry-run, payload validation or recovery semantics and would undermine the safety requirements of #38.

Mutating extension operations must therefore receive explicit domain-specific CLI commands once their canonical API and safety contract are known. `platform extension commands` is inspection only and never executes a command.

## Progressive domain commands

Generic extension reads complement, but do not replace, first-class commands. As domains such as capabilities/tools, agents/teams, nodes/workers, approvals or authentication become fully integrated into the Control Plane, #38 should add stable domain-specific command groups for their supported operations.

The architectural direction remains:

```text
platform CLI -> /api/v1 Control Plane -> canonical application services
```

No extension inspection command imports or contacts domain repositories, providers, workers, databases or adapters directly.
