# Error-boundary audit and migration guide

Status: mergeable audit/guardrail foundation for #983. This document is **non-normative**.

The canonical cross-boundary error model remains
`src/ai_multi_agent_platform/contracts/errors.py` (`ContractError`, `ErrorCode`, `retryable`, safe
structured `details`). The canonical northbound Control Plane translation remains
`ai_multi_agent_platform.control_plane.models.api_exception_from_contract` and its existing status
mapping. This guide records audit evidence and migration rules; it must not become a competing error
taxonomy.

## Scope and reproducible inventory

The audit covers production Python under `src/ai_multi_agent_platform` and separately recognizes
tests/fixtures, generated code and development tooling.

```bash
python scripts/ci/broad_exception_audit.py \
  --repository-root . \
  --format json
```

For a human-readable table use `--format markdown`. `--check` fails only for clearly prohibited
shapes; it is not a blanket ban on `except Exception`.

The inventory records stable finding metadata for each broad handler or relevant
`contextlib.suppress` context: file/line/scope/domain, exception form, observed action, re-raise,
translation, logging/telemetry signals, retryability signal, cancellation/shutdown risk,
diagnostic-text risk, canonical-boundary signal, recommended classification/action, source class,
review justification and severity.

### Search baseline captured during #983 preparation

The live default-branch search used by the preparation work found:

- `except Exception` in 98 production files;
- `except BaseException` in 12 production files;
- zero production bare `except:` occurrences;
- two production files using `contextlib.suppress(...)`;
- zero production `suppress(Exception)` occurrences;
- `str(exc)` in 76 production files.

These are file/search counts, not handler counts and not vulnerability counts. In particular,
`str(exc)` is only a review pool because provider or infrastructure exceptions can sometimes contain
request URLs, headers, connection strings, response bodies or user/tool payloads.

A temporary pytest-based inventory probe existed on the preparation branch solely to obtain CI
output. It is deliberately removed from the mergeable slice: permanent tests must assert behavior,
not emit issue-specific repository statistics. Handler-level totals remain reproducible from the
scanner for any exact commit and should be attached as evidence when a migration slice needs them.

## Classification model

Every production broad catch should end in one of four semantic categories.

### Boundary catch

A process, transport, adapter, provider or supervisor boundary where an implementation exception
must not escape northbound. Unknown failures are contained only at a true owner boundary and are
translated to canonical platform semantics or an established canonical result type.

### Cleanup / best effort

Secondary cleanup, cancellation settlement, rollback, destructor or telemetry work where failure is
intentionally subordinate to primary control flow. Broad cleanup is acceptable only when its reason
is explicit and cancellation/shutdown semantics remain authoritative.

### Domain error translation

Known provider/infrastructure exceptions are mapped to `ContractError` / `ErrorCode` at the owning
boundary. Translation should normally retain `__cause__` with `raise ... from exc`, set retryability
explicitly for operational failures and expose only allowlisted safe details.

### Unexpected swallowing

A broad catch hides a programming defect, invalid state, cancellation/shutdown, data problem or
unknown operational failure without an intentionally owned outer-boundary contract. Silent
`except Exception: return None`, `except BaseException: pass`, bare catches and equivalent broad
suppression are high-priority examples.

## Durable review marker

When a broad catch is intentionally retained and its purpose is not structurally self-evident, use
one reviewed marker immediately above the handler:

```python
# error-boundary: allow-broad-catch=boundary provider SDK outer boundary
except Exception as exc:
    ...
```

Allowed kinds are `boundary`, `cleanup` and `translation`. The marker is an assertion reviewed with
the code; it is not a suppression escape hatch and should be removed when the code shape no longer
matches its reason.

## Cancellation and shutdown

`asyncio.CancelledError`, `KeyboardInterrupt`, `SystemExit` and process shutdown must not become
ordinary provider/backend failures.

Most legitimate production `except BaseException` sites are expected to be true settlement/cleanup
boundaries that unconditionally re-raise the original throwable after releasing resources or
settling offloaded work. Such handlers should not be mechanically changed to `Exception` merely to
reduce a metric.

A nested `except Exception: pass` can also be valid when it only observes a worker that has already
settled and the enclosing `CancelledError` handler unconditionally re-raises cancellation. The
scanner has a characterization fixture for that exact shape.

Conversation/task streaming deserves focused migration tests: task cancellation and process signals
must propagate, while ordinary task failures may be contained by the stream owner and normalized.

## Canonical mapping guidance

| Source failure | Canonical mapping | Retryable default | Public diagnostics |
| --- | --- | --- | --- |
| provider/service unavailable | `UNAVAILABLE` or established routing equivalent | yes when operational | provider/operation IDs, exception type |
| provider timeout | `TIMEOUT` | yes | provider/operation + safe timeout metadata |
| rate limit | `RATE_LIMITED` | yes | provider + safe retry metadata |
| authentication | `UNAUTHORIZED` | no | provider/operation, never credentials |
| authorization/policy | `FORBIDDEN` | no | stable resource/policy IDs |
| invalid provider response | `INVALID_PROVIDER_RESPONSE` | normally no | provider/operation/validation category |
| transient network/storage failure | `UNAVAILABLE` or `TRANSIENT_FAILURE` | yes | operation/backend kind |
| non-transient backend/storage failure | `BACKEND_ERROR` | no | operation + safe backend identity |
| execution timeout/cancel/failure | existing `TIMEOUT` / `CANCELLED` / transient/permanent code | explicit | canonical task/run/step IDs |
| unknown implementation failure at true outer boundary | `BACKEND_ERROR` unless an established result contract owns it | no | exception type + correlation IDs only |

This table is guidance derived from the existing taxonomy; it does not add new `ErrorCode` values.
Existing domain-specific canonical codes/results remain authoritative where already defined.

## Causal chains and diagnostic safety

At translation boundaries:

```python
try:
    await provider_call()
except ProviderTimeout as exc:
    raise ContractError(
        ErrorCode.TIMEOUT,
        "provider request timed out",
        retryable=True,
        details={"operation": "generate"},
    ) from exc
```

Rules:

- preserve internal causes for debugging/telemetry;
- never serialize `__cause__`, provider SDK objects or raw requests northbound;
- never put credentials, headers, connection strings or secret values in public `details`;
- avoid raw `str(exc)` in public diagnostics unless that exception family has a reviewed safe-message
  contract;
- prefer stable operation/category/resource identifiers and `type(exc).__name__`;
- re-raise an existing canonical `ContractError` instead of wrapping it again unless the owning
  contract explicitly requires a different canonical error.

The audit command intentionally emits only allowlisted structural metadata. It never emits source
text, exception objects or exception messages. Dynamic exception expressions are normalized rather
than echoed from source.

## Logging and telemetry ownership

Translate low and log once at the boundary that owns the failed operation.

- Provider/storage/tool adapters translate known implementation failures and retain causes.
- The owning operation boundary emits structured telemetry with request/task/run/correlation context
  where available.
- Intermediate layers should not duplicate the same exception log while re-raising.
- Expected transient operational failures must remain distinguishable from programming defects and
  contract violations.
- Cleanup failure should not replace the primary failure unless cleanup itself leaves a meaningful
  partial mutation, such as failed compensation.
- Telemetry exporter failure must not recursively invoke the same failing telemetry path.

## Coordination with adjacent work

### #896 maintainability

#896 owns generic module/function size, complexity and repository-wide maintainability reporting.
The broad-exception scanner owns only error-boundary semantics. It must not grow into a second
complexity dashboard.

### #892 persistence

The Research persistence seam has active/recent backend-neutrality work. Persistence migrations under
#983 must re-read current #892 state before edits. The durable scanner deliberately contains no
issue-specific path exceptions or issue-numbered policy fields.

### CI consolidation

#1018 has landed, so a later #983 slice may add the broad-exception ratchet to the consolidated
`repository-quality.yml`. This first mergeable slice does not make the historical repository fail
wholesale: the current inventory still contains review/migration work. CI integration should compare
against an exact baseline or enforce only reviewed prohibited additions so existing debt is not
silently grandfathered as acceptable behavior.

## Characterization coverage in this slice

The behavior-oriented tests cover:

- silent `except Exception: return None` as prohibited;
- `BaseException` cleanup with unconditional re-raise as allowed;
- conditional re-raise of process signals as insufficient;
- explicit reviewed boundary markers;
- bare catches and broad `suppress(Exception)` as prohibited;
- typed `suppress(OSError)` as allowed cleanup inventory;
- raw exception-text use as a diagnostic review signal without echoing source text;
- cancellation settlement where nested worker observation is best-effort but outer cancellation
  still re-raises;
- separation of development tooling from production enforcement;
- normalization of dynamic exception expressions;
- stable Control Plane `ContractError` status/code/retryability serialization;
- proof that an internal exception cause containing a synthetic credential-like value is not copied
  into public Control Plane fields.

## Migration order after the guardrail foundation

1. Fix any `BaseException` site that can convert `KeyboardInterrupt`/`SystemExit` into ordinary work
   failure; preserve legitimate unconditional settlement/re-raise sites.
2. Remove silent broad swallowing or make the owning boundary/cleanup contract explicit and
   observable.
3. Review provider/model/network adapters for known SDK exceptions, explicit retryability and safe
   `ContractError` translation.
4. Review tool/workspace/execution/worker boundaries for equivalent canonical translation and
   redaction.
5. Review persistence after its active ownership seams are stable.
6. Add a baseline-aware CI ratchet in the consolidated repository-quality workflow.

#983 remains open after this foundation lands; it closes only when the repository-wide migration,
observability and CI acceptance criteria are satisfied.
