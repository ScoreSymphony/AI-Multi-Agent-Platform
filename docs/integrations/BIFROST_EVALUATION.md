# Bifrost optional model-gateway evaluation

Status: `experimental_only`  
Issue: #859  
Reviewed: 2026-09-12

This document evaluates `maximhq/bifrost` as an **optional** self-hosted model gateway behind the platform-owned `ModelProvider`, `ModelRegistry` and `ModelRouter` architecture. It is not an adoption approval and it does not make Bifrost part of baseline infrastructure.

The current result is deliberately `experimental_only`. Upstream/license/security and architecture-fit evidence are strong enough to continue evaluation, but representative live latency/throughput, streaming/tool-calling, SSRF/egress and restart/upgrade evidence are still required before the result can be promoted to `supported_optional`.

## Reviewed upstream

| Field | Reviewed value |
| --- | --- |
| Repository | `https://github.com/maximhq/bifrost` |
| HTTP transport tag | `transports/v2.1.1` |
| Reviewed commit | `c193745d2a713e9f58f021d43e138df5eb7e038a` |
| Release date | 2026-09-09 |
| Bundled core | `v1.8.6` |
| License | Apache-2.0 at the reviewed commit |
| Provenance | `upstream/bifrost.yaml` |

The reviewed upstream README describes a self-hostable HTTP gateway with a single OpenAI-compatible API, 23+ provider integrations, automatic fallbacks, load balancing and streaming/multimodal support. Its quick-start surface exposes `/v1/chat/completions`, which fits the platform's existing OpenAI-compatible provider transport without requiring Bifrost-native request/response types.

These are upstream capability claims until the corresponding platform tests are run. Provider-specific feature fidelity can differ by downstream provider/model and must not be inferred from the gateway-wide feature list.

## Architecture fit

The intended boundary is:

```text
Task / Agent requirements
        -> platform ModelRouter
        -> canonical ModelConfiguration
        -> selected ModelProvider instance
        -> existing OpenAI-compatible provider transport
        -> optional Bifrost HTTP gateway
        -> already-authorized downstream model/provider target
```

Bifrost is therefore **not**:

- canonical model inventory;
- the owner of platform routing policy;
- a replacement for `ModelRouter`;
- an authorization or approval authority;
- a secret store for canonical resources;
- required for startup, model routing or direct-provider operation.

The existing `OpenAICompatibleModelProvider` is sufficient for the evaluation. A dedicated `BifrostModelProvider` should be added only if future supported behavior needs Bifrost-specific health, capability or observability metadata that cannot be represented safely through the generic endpoint path.

Provider-native model identifiers remain adapter/deployment configuration. Canonical responses must continue returning the selected canonical model configuration ID; Bifrost aliases/provider names must not leak into `ModelResponse.model_ref`.

## Routing and fallback ownership

Bifrost offers its own weighted provider/key selection, retries and automatic fallbacks. Those features are useful operationally but create a second routing layer.

For this platform the precedence is binding:

1. `ModelRouter` resolves the allowed canonical candidate and platform policy.
2. Authorization/locality/capability restrictions are already applied before gateway invocation.
3. A Bifrost target may load-balance only among downstream targets that are semantically equivalent and already permitted by the platform decision.
4. Gateway fallbacks must not escape the platform-approved provider/model/locality set.
5. Any gateway-side decision that materially changes the downstream target must be observable as namespaced adapter metadata before it can be enabled in a supported profile.

The safest first supported configuration, if promotion occurs, is therefore a pinned gateway target with hidden cross-provider fallbacks disabled. More advanced Bifrost routing would require its own policy-conformance evidence.

## Comparison with existing paths

| Path | Architectural role | Current platform status | Extra service/hop | Routing ownership |
| --- | --- | --- | --- | --- |
| Direct provider adapter | baseline/reference path | supported | no gateway | platform only |
| LiteLLM | optional compatibility gateway/library | supported optional through #11 | proxy mode: yes | platform; advanced LiteLLM routing intentionally not baseline |
| Bifrost | optional HTTP gateway candidate | `experimental_only` | yes | platform must remain authoritative |

Bifrost's Go gateway may offer attractive throughput and operational features, but that is not sufficient evidence by itself. The comparison must use the **same canonical model and same local downstream endpoint** so model generation time does not masquerade as gateway overhead.

## Security review

### Upstream patch policy

The reviewed `SECURITY.md` says only the latest minor release of each supported major version receives security patches and recommends running the latest version. A supported platform integration therefore cannot use an unmaintained long-lived pin without an explicit update process.

The same policy notes that Bifrost handles provider API keys, that gateway/admin exposure should be restricted, and that plugins execute inside the request pipeline with access to request/response data. These are separate trust surfaces even for a self-hosted deployment.

### 2026 SSRF advisory

The evaluation explicitly includes `GHSA-w98g-5w9p-p3rc` / `CVE-2026-55245` (High). The advisory affected `github.com/maximhq/bifrost/core` versions before `v1.5.17` and concerned incomplete SSRF address blocking in server-side URL fetching. The fixed range starts at `v1.5.17`.

The reviewed `transports/v2.1.1` release bundles core `v1.8.6`, so it is newer than the fixed version. That is necessary but not sufficient to treat the gateway as trusted. A regression campaign must still exercise:

- IPv4 loopback;
- RFC1918/private IPv4;
- IPv4 link-local and cloud metadata targets;
- CGNAT (`100.64.0.0/10`);
- IPv6 loopback/link-local/site-local;
- IPv4-mapped IPv6;
- 6to4 (`2002::/16`);
- NAT64 (`64:ff9b::/96`);
- redirects from public to blocked targets;
- DNS rebinding / resolution-time changes;
- encoded/alternative host representations where the relevant Bifrost feature accepts URLs.

A local gateway is still network-capable software. Local placement does not satisfy the platform #43 egress/SSRF boundary by itself.

### Secrets and authority

Supported operation must preserve these platform rules:

- provider credentials are supplied only through #34 secret references/environment integration;
- plaintext credentials are absent from canonical configuration, reports, logs and normal diagnostics;
- Bifrost virtual keys/governance do not grant platform authorization;
- #15 authorization/approval remains authoritative before model/tool/external-side-effect access;
- gateway logs/telemetry must be configured consistently with platform redaction requirements;
- plugins are separately trusted code and are not implicitly approved because Bifrost itself is approved.

The #859 benchmark intentionally records provider IDs, timings, aggregate canonical errors and identity-conformance only. It excludes prompts/responses, HTTP headers, secret values and raw exception messages.

## Operations, persistence and clustering

The upstream project supports a simple local gateway path and also exposes broader storage/Helm/cluster configuration. Those larger deployments can add PostgreSQL/config-store/log-store, membership/counter synchronization and other service state.

For the platform this means:

- single-gateway operation is the first evaluation target;
- Bifrost persistence is not canonical model registry or Task/Run state;
- clustered Bifrost operation is not required for baseline or first support;
- restart/upgrade tests must verify that temporary gateway unavailability maps to canonical provider availability/errors without corrupting platform model inventory;
- removing Bifrost must only require repointing the affected provider configuration, not migrating canonical Agents/Tasks/models.

## Reproducible performance harness

The repository provides `ai_multi_agent_platform.benchmarking.model_gateway_evaluation` plus a CLI module. The harness executes identical canonical requests through a direct endpoint and Bifrost, with an optional LiteLLM third target. It records p50/p95/p99 end-to-end latency, throughput, canonical error categories and whether canonical model identity was preserved.

Example with the same local downstream model exposed directly and through Bifrost:

```bash
python -m ai_multi_agent_platform.benchmarking.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --direct-base-url http://127.0.0.1:8000/v1 \
  --direct-model local-model \
  --bifrost-base-url http://127.0.0.1:8080/v1 \
  --bifrost-model local-model \
  --operation-count 100 \
  --concurrency 8 \
  --warmup-operations 10 \
  --output artifacts/issue-859-direct-vs-bifrost.json
```

Optional LiteLLM comparison:

```bash
python -m ai_multi_agent_platform.benchmarking.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --direct-base-url http://127.0.0.1:8000/v1 \
  --direct-model local-model \
  --bifrost-base-url http://127.0.0.1:8080/v1 \
  --bifrost-model local-model \
  --litellm-base-url http://127.0.0.1:4000/v1 \
  --litellm-model local-model \
  --operation-count 100 \
  --concurrency 8 \
  --warmup-operations 10 \
  --output artifacts/issue-859-direct-vs-gateways.json
```

If a gateway requires a credential, pass the **environment-variable name**, never the value, for example `--bifrost-api-key-env BIFROST_TEST_TOKEN`.

No universal latency budget is invented in #859. The evidence must record the representative hardware, downstream server/model, gateway versions and workload; the adoption decision then compares the operational benefit with the measured overhead.

## Live test environment

`tests/test_issue_859_bifrost_evaluation.py` contains an opt-in integration/performance test. It is skipped unless these variables are present:

```text
BIFROST_EVAL_DIRECT_BASE_URL
BIFROST_EVAL_DIRECT_MODEL
BIFROST_EVAL_BIFROST_BASE_URL
BIFROST_EVAL_BIFROST_MODEL
```

Optional variables:

```text
BIFROST_EVAL_DIRECT_API_KEY_ENV
BIFROST_EVAL_BIFROST_API_KEY_ENV
BIFROST_EVAL_OPERATION_COUNT
BIFROST_EVAL_CONCURRENCY
BIFROST_EVAL_WARMUP
```

The `*_API_KEY_ENV` values are names of environment variables containing credentials; they are not credential values themselves.

## Evidence / acceptance matrix

| Requirement | Evidence at this review | State |
| --- | --- | --- |
| Exact upstream revision/version | `transports/v2.1.1`, commit `c193745...`, core `v1.8.6` | complete |
| Apache-2.0 verification | license inspected at reviewed commit | complete |
| Security advisory/patch posture | GHSA/CVE reviewed; current core newer than fix; upstream patch policy recorded | complete for desk review |
| Canonical #10 architecture fit | generic OpenAI-compatible `ModelProvider`; platform router remains owner | complete |
| Bifrost absent without changing normal route | no package/core dependency; candidate is external endpoint only | complete architecturally |
| Canonical ID leak regression | deterministic benchmark unit coverage | complete |
| Synthetic-secret/error redaction | report excludes raw exception messages and values; unit coverage | complete for harness |
| OpenAI chat request/response | upstream surface verified; platform generic transport exists | live evidence pending |
| Streaming conformance | upstream claims streaming; platform streaming provider exists | **pending live evidence** |
| Tool-calling conformance | gateway/provider-dependent | **pending live evidence** |
| Direct vs Bifrost latency/throughput | reproducible harness committed | **pending measured evidence** |
| Direct vs LiteLLM vs Bifrost cost | optional three-target harness committed | **pending measured evidence** |
| SSRF/egress regression | test matrix defined from advisory/#43 | **pending isolated runtime evidence** |
| Upstream unavailable/failover behavior | canonical error policy defined | **pending live fault evidence** |
| Restart and upgrade behavior | required procedure defined | **pending live evidence** |
| Clustering/persistence | reviewed as optional operational surface | **not required for first support; deeper evidence deferred** |

## Decision

**Current outcome: `experimental_only`.**

Reasons:

1. The architecture fit is good because the existing generic OpenAI-compatible provider can target Bifrost without redefining canonical model identity or routing ownership.
2. The reviewed source is Apache-2.0 and the pinned release is newer than the known 2026 SSRF fix.
3. Bifrost offers meaningful optional gateway functionality and is therefore worth completing the evaluation rather than rejecting it.
4. A high-severity SSRF history plus a broad gateway/plugin/network surface makes runtime security evidence mandatory.
5. The issue explicitly requires representative performance, streaming/tool and operational measurements; those cannot be inferred from upstream claims or synthetic unit tests.

### Promotion to `supported_optional`

Promotion requires all of the following on the same reviewed/pinned release (or a newer separately reviewed revision):

- live OpenAI-compatible request/response conformance passes;
- native streaming conformance passes for the supported platform profile;
- supported tool-calling/structured-output cases pass or are explicitly capability-gated;
- direct vs Bifrost and direct vs LiteLLM performance evidence is recorded on representative local hardware/endpoints;
- no provider-native identity escapes the canonical model boundary;
- synthetic-secret and real configuration diagnostics remain redacted;
- SSRF/egress regression passes, including redirect and DNS/address edge cases applicable to enabled URL-fetching features;
- unavailable-upstream, gateway restart and upgrade/restart behavior preserve canonical registry/router state;
- gateway routing/fallback configuration is proven not to bypass platform locality/capability/authorization restrictions;
- ordinary platform tests and startup remain valid with no Bifrost package/service installed.

Only after those gates pass should #799 surface Bifrost in **Advanced** setup as a `supported` optional gateway. It should not become the recommended/default model routing layer; `ModelRouter` remains the default and canonical authority.
