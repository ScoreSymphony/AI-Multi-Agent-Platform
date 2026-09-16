# Issue #983 provider / model / network / adapter error-boundary audit

Scope: provider adapters, model-provider boundaries, external HTTP/network transports,
streaming, and external execution/sandbox adapters.

Audit base: `main` at chat start, commit
`b54708094062730424a8621bbd85e0906189bb19`.

This is a scope-specific handoff. The central `ERROR_BOUNDARY_AUDIT.md` and the
repository-wide ratchet are intentionally not modified because other #983 slices are
running in parallel.

## Canonical policy used by this slice

- Northbound model/orchestrator failures use `ContractError` and existing `ErrorCode`
  values only. No provider-specific public exception hierarchy was added.
- External execution adapters continue to use the canonical `ExecutionResult` /
  `ExecutionError` contract; provider-private failures do not escape as SDK exceptions.
- Known transient conditions are explicitly retryable: provider timeout, network/service
  unavailability, HTTP 429, provider 5xx, and executor timeout.
- Unknown SDK/transport implementation failures are contained only at a real external
  adapter boundary and are non-retryable by default.
- `asyncio.CancelledError`, `KeyboardInterrupt`, and `SystemExit` are process-control
  signals, not provider failures. They propagate.
- Canonical request-token cancellation remains a normal cancelled execution result and is
  deliberately distinct from task cancellation.
- Provider payloads, SDK exception messages, credentials, and arbitrary provider metadata
  are not copied into canonical error messages/details. Safe structural data is limited to
  items such as exception type, HTTP status, provider status, and allow-listed metadata.

## Productive broad-catch inventory and decisions

| Location / symbol | Original pattern | Classification | Decision | Canonical semantics / retryability | Regression coverage |
| --- | --- | --- | --- | --- | --- |
| `adapters/openai_compatible.py::_request` | timeout + `CancelledError` + `OSError`; unknown transport exceptions escaped | external model transport boundary | **TRANSLATE** unknown ordinary exceptions; **REMOVE** cancellation normalization | timeout -> `TIMEOUT` retryable; `OSError` -> `UNAVAILABLE` retryable; unknown -> `BACKEND_ERROR` non-retryable; cancellation/process-control propagate | OpenAI provider unit tests cover timeout, network, unknown SDK failure, cancellation, `KeyboardInterrupt`, `SystemExit`, and secret redaction |
| `adapters/litellm.py::_generate_library` | `CancelledError` translated to `ContractError(CANCELLED)`; broad `except Exception` SDK mapping | true LiteLLM SDK boundary | **REMOVE** cancellation translation; **KEEP + JUSTIFY** broad SDK translation | existing typed/class-name mapping retained; rate-limit/timeout/unavailable remain retryable; unknown -> `BACKEND_ERROR` non-retryable | LiteLLM unit test now requires raw task-cancellation propagation; existing tests cover timeout and common provider categories |
| `adapters/openai_compatible_streaming.py::Urllib...producer` | broad worker-thread `except Exception` forwarded raw exception through queue | misplaced transport catch / streaming boundary | **REMOVE** producer broad catch; producer task exception is observed by async consumer | thread failure reaches provider streaming boundary instead of being silently detached | provider-network regression tests plus producer-task observation path |
| `adapters/openai_compatible_streaming.py::_native_stream` | `CancelledError` translated to canonical error; timeout/OSError only; unknown stream exceptions escaped | true streaming provider boundary | **REMOVE** cancellation translation; **NARROW** malformed stream data; **TRANSLATE** unknown ordinary exceptions | malformed stream -> `INVALID_PROVIDER_RESPONSE` non-retryable; timeout -> `TIMEOUT` retryable; network -> `UNAVAILABLE` retryable; unknown -> `BACKEND_ERROR` non-retryable | regression tests cover malformed stream, unknown stream failure, secret redaction, cancellation |
| `adapters/hermes.py::_stop_best_effort` | `except (Exception, asyncio.CancelledError): return` | cleanup / best effort | **NARROW** to `ContractError`; cancellation must propagate | provider cleanup failures already normalized by `_request`; process-control is not swallowed | Hermes regression coverage exercises propagation through transport boundary |
| `adapters/hermes.py::_request` | timeout + `(ConnectionError, OSError)` only; network message included `str(exc)` | external Hermes HTTP boundary | **TRANSLATE** unknown ordinary exceptions and sanitize known network failures | timeout -> `TIMEOUT` retryable; network -> `UNAVAILABLE` retryable; unknown -> `BACKEND_ERROR` non-retryable | regression tests cover network, unknown SDK error, cancellation/process-control, secret redaction |
| `adapters/hermes.py::_raise_for_status` and terminal-run failure path | arbitrary provider `detail` / `error` text was appended to northbound error messages | provider response boundary | **NARROW / SANITIZE** | 401 -> `UNAUTHORIZED`; 429 -> `RATE_LIMITED` retryable; 5xx -> `UNAVAILABLE` retryable; safe `http_status`/`provider_status` only | regression tests verify auth/rate-limit/5xx mapping and that provider payload secrets are absent |
| `adapters/agent_sandbox.py::health` | broad `except Exception` -> unhealthy descriptor | external provider health boundary | **KEEP + JUSTIFY** | health is diagnostic and must degrade on arbitrary ordinary SDK failures; BaseException signals still propagate | existing health contract + source marker |
| `adapters/agent_sandbox.py::execute` | broad provider catch marked unknown failures retryable; `CancelledError` also used for request-token cancellation | external execution adapter boundary | **TRANSLATE** unknown failures non-retryably; split token cancellation from task cancellation | timeout -> retryable canonical timeout result; unknown -> internal non-retryable result; task cancellation propagates; token cancellation returns cancelled result | shared external-adapter regression tests |
| `adapters/agent_sandbox.py::_cancel_backend` | broad `except Exception: return` | cleanup / best effort | **KEEP + JUSTIFY** | only ordinary provider cleanup failures are suppressed; BaseException signals propagate | shared cancellation regression tests |
| `adapters/openshell.py::health` | broad `except Exception` -> unhealthy descriptor | external provider health boundary | **KEEP + JUSTIFY** | same health-boundary rationale as Agent-Sandbox | existing health contract + source marker |
| `adapters/openshell.py::execute` | unknown provider failure retryable; task cancellation returned normal cancelled result | external execution adapter boundary | **TRANSLATE** unknown non-retryably; split token/task cancellation | timeout retryable; unknown non-retryable; task cancellation propagates | shared external-adapter regression tests |
| `adapters/openshell.py::_cancel_backend` | explicit `CancelledError` was swallowed; broad ordinary cleanup catch | cleanup / best effort | **REMOVE** cancellation swallowing; **KEEP + JUSTIFY** ordinary cleanup catch | parent task cancellation now cancels provider-cancel task and re-raises | shared task-cancellation regression tests |
| `adapters/swe_rex.py::health` | broad `except Exception` -> unhealthy descriptor | external provider health boundary | **KEEP + JUSTIFY** | diagnostic health degradation; BaseException signals propagate | existing health contract + source marker |
| `adapters/swe_rex.py::execute` | broad provider catch retryable; task/token cancellation conflated | external execution adapter boundary | **TRANSLATE** unknown non-retryably; split cancellation semantics | timeout retryable; unknown internal failure non-retryable; task cancellation propagates | shared external-adapter regression tests |
| `adapters/swe_rex.py::_cancel_backend` | broad `except Exception: return` | cleanup / best effort | **KEEP + JUSTIFY** | ordinary cleanup failure suppressed, process-control propagates | shared cancellation regression tests |
| `adapters/swe_rex.py::_translate_result` | provider error message/stdout/stderr/output/resources and unknown error code could escape for infrastructure failures | provider response / evidence boundary | **SANITIZE** | infrastructure/unknown provider failures expose generic message, allow-listed metadata and known safe error code only; provider-declared retryability retained for known result | dedicated SWE-ReX secret-leak regression test |
| `adapters/skillspector.py` raw-report retention block | broad `except Exception` records `raw_report_retention_failed` | best-effort evidence retention / storage side-boundary | **KEEP + JUSTIFY**, no code change | failure to retain optional raw evidence must not replace the primary evaluation result; no process-control caught | documented here; persistence/storage ownership intentionally not expanded |

No `except BaseException` remained in the audited adapter set at the audit base.

## Streaming and cleanup notes

The urllib streaming producer previously caught arbitrary `Exception` in its worker
thread and delivered it through a side-channel. The broad catch was removed. On the
`done` signal the async iterator now awaits the producer task, so a producer failure is
observed and translated at `_native_stream`, the actual provider boundary. The iterator
cleanup still sets the stop flag, cancels an unfinished wrapper task, and gathers it with
`return_exceptions=True`; cleanup therefore does not replace an already active stream
error or task cancellation.

Hermes best-effort stop and the sandbox provider-cancel paths suppress only ordinary
cleanup failures where appropriate. They no longer consume task cancellation.

## Secret-handling decisions

The following previously unsafe northbound data paths were removed or constrained:

- Hermes no longer appends arbitrary HTTP `detail`/`error` payload text to
  `ContractError.message`.
- Hermes terminal-run failures no longer expose `snapshot.error`.
- Hermes network failures no longer append `str(exc)`.
- Unknown OpenAI-compatible, streaming, and Hermes SDK/transport failures expose only
  the exception class name as structured detail.
- SWE-ReX infrastructure/unknown provider results now follow the same redaction model
  already used by Agent-Sandbox and OpenShell.

Underlying Python causes are retained with `raise ... from exc` where the exception is
useful for local debugging, but untrusted exception text is never copied into canonical
messages/details or adapter metadata. Callers must serialize `ContractError` fields, not
provider exception objects or tracebacks, across northbound/API boundaries.

## Cross-slice handoff findings (not changed here)

The following broad catches were observed while checking adjacent provider/network
surfaces but are outside this slice's ownership and were deliberately left untouched:

- `src/ai_multi_agent_platform/models/routing_profile_repository.py`: broad rollback
  catches around `_persist()` are persistence/repository semantics; persistence owner
  should classify them if they were introduced after the completed persistence slice.
- `src/ai_multi_agent_platform/observability/model_provider.py` and
  `observability/instrumentation.py`: observability owner.
- `src/ai_multi_agent_platform/benchmarking/model_gateway_evaluation.py`,
  `benchmarking/provider_faults.py`, and `benchmarking/transport_faults.py`: benchmark /
  fault-injection owner.
- `src/ai_multi_agent_platform/messaging/network.py`,
  `distributed/transport.py`, `distributed/workspace_transport_endpoint.py`, and
  `application_distribution/distributed_execution.py`: distributed/messaging owner.
- generic capability/worker/tool execution catches remain outside this provider-adapter
  slice unless they are later proven to be the direct external adapter boundary.

MCP was not re-audited; only #983-specific provider gaps outside the completed #1094
work were considered.

## Remaining final-#983 work

The final repository-wide #983 pass should reconcile this scope-specific inventory with
parallel slice handoffs, verify no new provider/adapter broad catches landed on `main`
after the audit base, and apply the repository-wide guardrail/ratchet decision centrally.
Issue #983 remains open after this slice.
