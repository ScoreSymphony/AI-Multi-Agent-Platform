# Canonical usage inspection CLI

Issue: #38
Domain: #76 Usage and resource accounting

The CLI exposes the three read-only accounting collections already registered on the versioned Control Plane and consumed by the Web UI.

## Architecture boundary

```text
platform CLI -> /api/v1 Control Plane -> accounting resource services
```

The CLI never opens the accounting store, telemetry backend, model provider, worker, node, filesystem, or database directly. If the accounting collections are not registered, the canonical Control Plane error is surfaced; there is no backend fallback.

## Commands

### Usage records

```bash
platform usage record list
platform usage record show USAGE_RECORD_ID
```

Records expose canonical metric type, quantity/unit, measurement quality, aggregation mode, scope, provider/source provenance, timestamps and optional cost metadata exactly as returned by `usage-records`.

### Usage aggregates

```bash
platform usage aggregate list
platform usage aggregate show USAGE_AGGREGATE_ID
```

Aggregates expose canonical totals or latest point-in-time values according to `aggregation_mode`, measurement-quality counts, scope and trend data from `usage-aggregates`.

### Usage budgets

```bash
platform usage budget list
platform usage budget show USAGE_BUDGET_ID
```

Budgets expose the canonical configured limit and current consumed/remaining/threshold state from `usage-budgets`. These commands are inspection-only; the CLI does not create or mutate budgets through an unowned generic command.

## Shared list options

All three `list` commands use the standard CLI list contract, including:

```bash
--limit N
--cursor CURSOR
--sort FIELD
--direction asc|desc
--q QUERY
--filter FIELD=VALUE
--fields FIELD1,FIELD2
```

The CLI only serializes these options into the canonical Control Plane query. Filtering, visibility, owner isolation and accounting semantics remain server-authoritative.

## Web UI parity

The Web UI Resources/Usage surface and these commands use the same Control Plane collections:

- `usage-records`
- `usage-aggregates`
- `usage-budgets`

This gives operators a headless view of the same canonical accounting resources without introducing a second usage store or CLI-specific accounting interpretation.
