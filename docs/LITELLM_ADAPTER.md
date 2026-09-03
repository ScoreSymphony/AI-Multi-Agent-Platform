# LiteLLM optional model-gateway adapter

Issue #11 adds LiteLLM only behind the platform-owned model contracts from #5/#10. LiteLLM is a convenience integration, not canonical model inventory, routing policy, Agent configuration or Task lifecycle state.

## Supported integration modes

### 1. Library mode

`LiteLLMModelProvider` keeps module imports dependency-free. When an enabled library provider instance is created without an injected test transport, it resolves the optional `litellm` package immediately so missing dependencies fail before registry attachment. It then calls `acompletion` with translated canonical request data.

Install only when this mode is wanted:

```bash
python -m pip install -e ".[litellm]"
```

The baseline package and normal core test suite do not install this extra.

### 2. Proxy/service mode

`LiteLLMModelProvider` can target a separately deployed LiteLLM Proxy through its OpenAI-compatible HTTP API. The implementation deliberately reuses the existing `OpenAICompatibleModelProvider` transport instead of introducing a second HTTP protocol stack.

Proxy mode therefore does not require the `litellm` Python package inside the platform process.

### 3. Generic OpenAI-compatible path

When LiteLLM-specific adapter metadata is unnecessary, the existing `OpenAICompatibleModelProvider` can point directly at a LiteLLM Proxy. This is the simplest supported path for deployments that only need an OpenAI-compatible gateway endpoint.

## Routing ownership and precedence

The canonical order is:

```text
Task / Agent requirements
        -> platform ModelRouter
        -> canonical ModelConfiguration
        -> selected ModelProvider instance
        -> LiteLLM adapter
        -> one configured LiteLLM/native model target
```

The platform router owns canonical model policy. It resolves capability requirements, explicit canonical assignments, model health and local/self-hosted restrictions before a provider invocation is made.

The baseline LiteLLM library adapter intentionally performs a direct `acompletion` call for the already selected target and does **not** configure LiteLLM Router fallbacks/load balancing. This prevents a hidden second routing layer from escaping platform constraints. A future opt-in LiteLLM Router integration must restrict its candidate set to targets already permitted by platform policy and must expose the resulting decision as namespaced observability metadata.

Canonical Agents and Tasks reference stable platform model configuration IDs or routing requirements. LiteLLM model strings and proxy aliases stay inside adapter configuration.

## Canonical translation

The adapter maps the rich canonical model protocol into OpenAI-style/LiteLLM request fields:

- canonical messages -> `messages`;
- canonical model configuration ID -> configured LiteLLM/proxy model name;
- tool definitions -> function tools;
- JSON-object / JSON-schema expectations -> `response_format`;
- generation parameters -> temperature/top-p/max-tokens/seed/stop;
- request timeout -> adapter timeout and coroutine deadline.

Responses are normalized back into `ModelResponse` plus `model-protocol` metadata so `CanonicalModelResponse.from_contract_response()` can recover:

- text content;
- tool-call requests;
- structured JSON output;
- normalized finish reason;
- usage;
- timing;
- correlation/task/run/agent context.

No LiteLLM-native response type crosses the `ModelProvider` boundary.

## Configuration

See `config/litellm.example.json` for library and proxy examples.

`LiteLLMProviderConfig` supports:

- `enabled`;
- `mode` (`library` or `proxy`);
- stable provider ID;
- canonical-model-ID -> LiteLLM/proxy-model mapping;
- optional base URL;
- credential environment-variable reference;
- timeout;
- library-mode retry count (`max_retries` -> LiteLLM `num_retries`);
- adapter telemetry/logging metadata mode (`platform_only` or `disabled`);
- additional non-secret HTTP headers.

`LiteLLMProviderConfig.from_mapping(...)` is the JSON/resolved-configuration boundary that turns provider-scope configuration into the validated runtime config object. Unknown keys are rejected. The committed examples are parsed through the platform configuration resolver and this boundary in tests.

A raw credential value is never part of canonical configuration or provider metadata. When `api_key_env` is configured, the value is resolved only at invocation/health time. Missing credentials fail as `invalid_configuration`.

## Retry, fallback, telemetry and locality policy

- Library mode forwards non-negative `max_retries` as LiteLLM `num_retries`; `0` is the local-first default. Request/control-plane timeouts still override the provider default timeout.
- Proxy mode does not add a second retry engine. Retry/load-balancing inside a separately deployed LiteLLM Proxy is deployment-owned and must not bypass the platform's canonical route selection.
- LiteLLM Router fallbacks remain intentionally disabled in this baseline. Provider fallbacks are therefore never silently enabled by adapter configuration.
- `telemetry_mode=platform_only` emits only platform-owned namespaced adapter metadata subject to platform redaction. `disabled` suppresses LiteLLM-specific per-request/error metadata. The adapter does not enable LiteLLM global callbacks or provider-native logging/telemetry.
- Local/self-hosted restrictions remain canonical `ModelConfiguration.location` plus `local_only` / `self_hosted_only` routing requirements. They are intentionally not duplicated as LiteLLM-native routing policy; tests prove a higher-priority remote LiteLLM-backed model cannot bypass either restriction.

## Local-first examples

Library mode can target a local model service supported by LiteLLM, for example an Ollama endpoint on `127.0.0.1:11434`. Proxy mode can target a local LiteLLM Proxy on `127.0.0.1:4000/v1` whose configured model itself points to a local/self-hosted backend.

Neither mode creates a paid-cloud requirement. The platform remains valid with the entire LiteLLM adapter disabled or removed.

## Health

- Disabled adapter -> `unavailable`.
- Proxy mode -> health delegates to the OpenAI-compatible proxy `/models` probe.
- Library mode -> health validates optional dependency availability and configured credential reference. Downstream provider/model failures remain invocation errors and feed normal Model Registry health handling.

## Error mapping

LiteLLM/provider exceptions are translated before leaving the adapter boundary. The first-pass mapping includes:

| Provider/LiteLLM failure | Canonical error |
| --- | --- |
| authentication/permission | `unauthorized` |
| context window exceeded | `input_too_large` |
| rate limit | `rate_limited` |
| timeout | `timeout` |
| missing model | `model_unavailable` |
| connection/service unavailable | `unavailable` |
| unsupported parameter/capability | `unsupported_capability` |
| bad/invalid request | `invalid_request` |
| transient/API failure | `transient_failure` |
| unknown backend exception | `backend_error` |

Only the upstream exception class name is retained in adapter diagnostics; raw exception objects do not escape into canonical APIs.

## Optional dependency and version policy

The library extra is pinned to `litellm==1.99.0` for this compatibility target. Core imports must remain valid without that package installed. The normal `dev` extra intentionally does not depend on LiteLLM.

The repository records provenance in `upstream/litellm.yaml`. The reviewed upstream license states that content outside `enterprise/` is MIT while `enterprise/` has separate licensing. This integration neither copies nor depends on Enterprise source.

Updates require an explicit version-change review and must rerun:

1. baseline CI without LiteLLM installed;
2. optional LiteLLM package import/compatibility check;
3. issue #11 adapter contract tests;
4. model registry/router tests;
5. local/proxy integration fixture;
6. license/security/provenance review.

## Removal / replacement

Removing LiteLLM means deleting the optional dependency, adapter/configuration and any separately deployed proxy. It does not require changes to canonical Task, Agent, ModelConfiguration or ModelRouter contracts. Those resources can be repointed to another `ModelProvider` implementation.
