# CLI extension inspection and execution

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

## Registered command execution

The current CLI also exposes an explicit registered-command transport:

```bash
platform extension execute CANONICAL_COMMAND RESOURCE_REF \
  --payload '{...}' \
  --idempotency-key UNIQUE_KEY
```

Before sending a mutation, the CLI verifies that `CANONICAL_COMMAND` appears in the Control Plane's `x-registered-extension-commands` list. It then sends the request only through:

```text
POST /api/v1/commands/{CANONICAL_COMMAND}
```

with the supplied `resource_ref`, JSON-object payload and explicit idempotency key. The CLI never imports the owning domain service or accesses its repository directly.

This generic transport does **not** invent domain semantics. Authorization, approval requirements, revision checks, payload validation, recovery and lifecycle rules remain owned by the canonical server-side command. Operators must use the owning domain's documented payload contract; discovery of a command name alone is not sufficient to infer safe arguments.

Where a domain needs additional client-side confirmation, dry-run handling or structured argument validation, it may still provide a dedicated top-level CLI command. Such convenience commands must resolve to the same canonical Control Plane operation rather than becoming an alternate execution path.

## Progressive domain commands

Generic extension reads/execution complement, but do not replace, first-class commands where stronger domain ergonomics are useful. As domains such as capabilities/tools, agents/teams, nodes/workers, approvals, authentication or Goals become fully integrated, their documented CLI surfaces must preserve the same architectural direction:

```text
platform CLI -> /api/v1 Control Plane -> canonical application services
```

No extension command imports or contacts domain repositories, providers, workers, databases, schedulers or adapters directly.
