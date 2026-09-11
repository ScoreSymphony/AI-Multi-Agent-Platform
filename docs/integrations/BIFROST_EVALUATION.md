# Bifrost optional model-gateway evaluation

Status: `experimental_only`  
Issue: #859  
Reviewed: 2026-09-12

This document evaluates `maximhq/bifrost` as an **optional** self-hosted model gateway behind the platform-owned `ModelProvider`, `ModelRegistry` and `ModelRouter` architecture. It is not an adoption approval and does not make Bifrost baseline infrastructure.

The current result remains deliberately `experimental_only`. Upstream/license/security and architecture-fit evidence are sufficient to continue evaluation, but representative live latency/throughput, OpenAI-compatible streaming/tool behavior, SSRF/egress, routing-policy and restart/upgrade evidence are still required before promotion to `supported_optional`.

The reproducible live procedure is documented in `docs/integrations/BIFROST_LIVE_EVIDENCE_RUNBOOK.md`.

## Reviewed upstream

| Field | Reviewed value |
| --- | --- |
| Repository | `https://github.com/maximhq/bifrost` |
| HTTP transport tag | `transports/v2.1.1` |
| Reviewed commit | `c193745d2a713e9f58f021d43e138df5eb7e038a` |
| Release date | 2026-09-09 |
| Bundled core | `v1.8.6` |
| Bundled framework | `v1.6.2` |
| License | Apache-2.0 at the reviewed commit |
| Provenance | `upstream/bifrost.yaml` |

The reviewed upstream exposes an OpenAI-compatible HTTP gateway and advertises broad provider coverage, fallbacks/load balancing, streaming and multimodal behavior. `/v1/chat/completions` fits the platform's existing OpenAI-compatible provider transport without requiring Bifrost-native canonical request/response types.

Those are upstream capability claims until corresponding platform live tests pass. Provider/model feature fidelity must not be inferred from a gateway-wide feature list.

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

The existing `OpenAICompatibleModelProvider` is sufficient for the evaluation. A dedicated `BifrostModelProvider` should be added only if supported behavior later needs Bifrost-specific health, capability or observability metadata that cannot be represented safely through the generic endpoint path.

Provider-native model identifiers remain adapter/deployment configuration. Canonical responses must continue returning the selected canonical model configuration ID; Bifrost aliases/provider names must not leak into `ModelResponse.model_ref`.

The benchmark contracts and evidence model remain under `ai_multi_agent_platform.benchmarking` and depend only on canonical `ModelProvider` contracts. The executable OpenAI-compatible benchmark composition lives under `ai_multi_agent_platform.adapters.model_gateway_evaluation_cli`; this preserves the repository invariant that core modules do not import concrete adapters.

## Routing and fallback ownership

Bifrost supports its own provider/key selection, retries, balancing and fallbacks. Those features can be operationally useful, but they create a second routing layer.

For this platform the precedence is binding:

1. `ModelRouter` resolves the allowed canonical candidate and platform policy.
2. Authorization/locality/capability restrictions are applied before gateway invocation.
3. A Bifrost target may load-balance only among downstream targets that are semantically equivalent and already permitted by the platform decision.
4. Gateway fallbacks must not escape the platform-approved provider/model/locality set.
5. Any gateway-side decision that materially changes the downstream target must be observable through namespaced adapter metadata before it can be enabled in a supported profile.

The safest first supported configuration, if promotion occurs, is a pinned explicit provider/model target with hidden cross-provider fallbacks disabled.

### Current upstream routing/governance caveats

The review also tracks unresolved upstream behavior rather than assuming all routing modes are equivalent:

- `maximhq/bifrost#3458` is open as of 2026-09-12. It reports that bare-model catalog resolution can eagerly choose `providers[0]` before governance/routing has made its final selection. That directly reinforces the platform requirement to use an **explicit provider/model target** for the first supported profile and not depend on bare-model catalog routing without live policy-conformance evidence.
- `maximhq/bifrost#2351` remains open/reopened and reports custom-provider virtual-key behavior requiring an explicit model allowlist on the originally reported Bifrost version. This review does **not** infer that `transports/v2.1.1` is affected or fixed. Virtual-key/custom-provider governance must be retested on the reviewed pin before that mode is considered supported.

These issues do not prove that the pinned release is unusable. They do mean that Bifrost-native governance/routing cannot substitute for platform policy and that advanced gateway routing must remain outside the initial support profile.

## Comparison with existing paths

| Path | Architectural role | Current platform status | Extra service/hop | Routing ownership |
| --- | --- | --- | --- | --- |
| Direct provider adapter | baseline/reference path | supported | no gateway | platform only |
| LiteLLM | optional compatibility gateway/library | supported optional through #11 | proxy mode: yes | platform; advanced LiteLLM routing is not baseline |
| Bifrost | optional HTTP gateway candidate | `experimental_only` | yes | platform must remain authoritative |

Bifrost's implementation may provide useful gateway throughput and operational features, but that cannot be accepted from upstream claims alone. Performance comparison must use the **same canonical model and same downstream deployment** so model generation time does not masquerade as gateway overhead.

## Security review

### Upstream patch policy

The reviewed upstream security policy says only the latest minor release of each supported major version receives security patches and recommends staying current. Its supported-version table does not line up cleanly with the reviewed transport/core release numbering, so support status must not be inferred from that table alone. A supported platform integration therefore needs an explicit update/review process.

Bifrost handles provider credentials and can expose gateway/admin/plugin surfaces. These remain separate trust surfaces even in a self-hosted deployment.

### 2026 SSRF advisory

The evaluation explicitly includes `GHSA-w98g-5w9p-p3rc` / `CVE-2026-55245` (High). The advisory affected `github.com/maximhq/bifrost/core` versions before `v1.5.17` and concerned incomplete SSRF address blocking in server-side URL fetching. The fixed range starts at `v1.5.17`.

The reviewed `transports/v2.1.1` release bundles core `v1.8.6`, so it is newer than the fixed version. That is necessary but not sufficient to treat the gateway as trusted. The isolated runtime regression campaign still covers:

- IPv4 loopback;
- RFC1918/private IPv4;
- IPv4 link-local and synthetic metadata-range targets;
- CGNAT (`100.64.0.0/10`);
- IPv6 loopback/link-local/site-local;
- IPv4-mapped IPv6;
- 6to4 (`2002::/16`);
- NAT64 (`64:ff9b::/96` plus relevant local-use form);
- Teredo (`2001:0000::/32`);
- public-to-blocked redirects;
- DNS rebinding / resolution-time changes;
- encoded/alternative host representations where the enabled Bifrost feature accepts URLs.

The reviewed core source includes regression coverage for the listed special address classes and re-validates resolution on each dial. That strengthens pin-level evidence but does not replace isolated deployment-level egress testing.

A local gateway is still network-capable software. Local placement does not satisfy the platform #43 egress/SSRF boundary by itself.

### Secrets and authority

Supported operation must preserve these platform rules:

- provider credentials are supplied only through #34 secret references/environment integration;
- plaintext credentials are absent from canonical configuration, benchmark artifacts, logs and normal diagnostics;
- Bifrost virtual keys/governance do not grant platform authorization;
- #15 authorization/approval remains authoritative before model/tool/external-side-effect access;
- gateway logs/telemetry must be configured consistently with platform redaction requirements;
- plugins are separately trusted code and are not implicitly approved because Bifrost itself is approved.

The #859 benchmark records timings, provider IDs, aggregate canonical errors, canonical-identity conformance, host/runtime metadata and explicit secret-free component/version/model evidence. It deliberately excludes prompts/responses, endpoint URLs, HTTP headers, credential values and raw exception messages. The workload prompt is persisted only as SHA-256 fingerprint plus length.

## Operations, persistence and clustering

The upstream project supports simple local gateway operation plus broader persistence/cluster deployment surfaces. Those larger deployments may add database, configuration/log stores, synchronization and additional network/service state.

For the platform:

- single-gateway operation is the first evaluation target;
- Bifrost persistence is not canonical model registry or Task/Run state;
- clustered Bifrost operation is not required for first support;
- restart/upgrade tests must verify temporary gateway unavailability maps to canonical provider availability/errors without corrupting platform model inventory;
- removing Bifrost must require only repointing the affected provider configuration, not migrating canonical Agents/Tasks/model IDs.

## Reproducible performance harness

The repository provides the provider-neutral `ai_multi_agent_platform.benchmarking.model_gateway_evaluation` and `model_gateway_evidence` modules plus the concrete OpenAI-compatible CLI at `ai_multi_agent_platform.adapters.model_gateway_evaluation_cli`. The harness executes identical canonical requests through a direct endpoint and Bifrost, with an optional LiteLLM third target.

It records:

- p50/p95/p99 end-to-end latency for successful operations;
- successful-operation throughput plus attempted/success/failure counts;
- canonical error categories;
- canonical model-identity preservation;
- platform commit/version;
- host OS/CPU/RAM/Python metadata;
- secret-free per-target component version/revision, native request-model identifier and common downstream deployment label.

Failed requests do not contribute to successful-operation throughput or latency distributions. A target with no successful calls therefore has no fabricated latency delta.

Example with the same downstream model exposed directly and through Bifrost:

```bash
python -m ai_multi_agent_platform.adapters.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --downstream-deployment-label same-vllm-local-instance \
  --direct-base-url http://127.0.0.1:8000/v1 \
  --direct-model local-model \
  --direct-runtime-version vllm-REPLACE_WITH_TESTED_VERSION \
  --bifrost-base-url http://127.0.0.1:8080/v1 \
  --bifrost-model provider/local-model \
  --operation-count 100 \
  --concurrency 8 \
  --warmup-operations 10 \
  --platform-commit "$(git rev-parse HEAD)" \
  --output artifacts/issue-859-direct-vs-bifrost.json
```

The CLI defaults its Bifrost evidence metadata to the reviewed `transports/v2.1.1` / core `v1.8.6` / `c193745...` pin. If the actual live target differs, the version/revision arguments must be overridden and that revision separately reviewed.

Optional LiteLLM comparison:

```bash
python -m ai_multi_agent_platform.adapters.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --downstream-deployment-label same-vllm-local-instance \
  --direct-base-url http://127.0.0.1:8000/v1 \
  --direct-model local-model \
  --direct-runtime-version vllm-REPLACE_WITH_TESTED_VERSION \
  --bifrost-base-url http://127.0.0.1:8080/v1 \
  --bifrost-model provider/local-model \
  --litellm-base-url http://127.0.0.1:4000/v1 \
  --litellm-model local-model \
  --litellm-version REPLACE_WITH_TESTED_VERSION \
  --operation-count 100 \
  --concurrency 8 \
  --warmup-operations 10 \
  --platform-commit "$(git rev-parse HEAD)" \
  --output artifacts/issue-859-direct-vs-gateways.json
```

If a gateway requires a credential, pass the **environment-variable name**, never the value, for example `--bifrost-api-key-env BIFROST_TEST_TOKEN`.

No universal latency budget is invented in #859. The evidence must record representative hardware, downstream runtime/model, gateway versions and workload; the adoption decision then compares measured overhead with operational benefit.

## Hermetic pinned-container compatibility lane

The repository CI contains a `bifrost-pinned-compat` job that runs the real `maximhq/bifrost:v2.1.1` image against a local OpenAI-compatible fixture without external model APIs or production credentials. It records the pulled image digest and exercises chat, streaming, structured output/tool-call pass-through, canonical model identity, controlled downstream unavailability and synthetic-secret diagnostics.

This lane is **compatibility/fault evidence**, not representative performance evidence. The synthetic mock model makes its emitted latency/throughput artifact useful for regression/smoke comparison only; it cannot satisfy the required real direct-vs-gateway performance decision.

## Live conformance environment

`tests/test_issue_859_bifrost_live_conformance.py` provides opt-in live streaming, structured-output, tool-calling and controlled-upstream-fault gates. The live performance comparison in `tests/test_issue_859_bifrost_evaluation.py` is skipped unless direct and Bifrost endpoints are configured.

Core environment variables include:

```text
BIFROST_EVAL_DIRECT_BASE_URL
BIFROST_EVAL_DIRECT_MODEL
BIFROST_EVAL_BIFROST_BASE_URL
BIFROST_EVAL_BIFROST_MODEL
```

Optional variables include:

```text
BIFROST_EVAL_DIRECT_API_KEY_ENV
BIFROST_EVAL_BIFROST_API_KEY_ENV
BIFROST_EVAL_OPERATION_COUNT
BIFROST_EVAL_CONCURRENCY
BIFROST_EVAL_WARMUP
BIFROST_EVAL_STRUCTURED_OUTPUT
BIFROST_EVAL_TOOL_CALLING
BIFROST_EVAL_UPSTREAM_CONTROL_URL
```

The `*_API_KEY_ENV` values are names of environment variables containing credentials; they are not credential values themselves.

## Evidence / acceptance matrix

| Requirement | Evidence at this review | State |
| --- | --- | --- |
| Exact upstream revision/version | `transports/v2.1.1`, commit `c193745...`, core `v1.8.6`, framework `v1.6.2` | complete |
| Apache-2.0 verification | license inspected at reviewed commit | complete |
| Security advisory/patch posture | GHSA/CVE reviewed; pin is newer than fix; patch-policy ambiguity recorded | complete for desk review |
| Canonical #10 architecture fit | generic OpenAI-compatible `ModelProvider`; platform router remains owner | complete |
| Core-to-adapter dependency direction | benchmark core is provider-neutral; concrete evaluator composition is in adapter layer | complete |
| Current routing/governance risk review | open upstream #3458/#2351 recorded with non-assumption rules | complete for desk review |
| Bifrost absent without changing normal route | no package/core dependency; candidate is external endpoint only | complete architecturally |
| Canonical ID leak regression | deterministic benchmark unit coverage | complete |
| Synthetic-secret/error redaction | report excludes prompt/endpoint/secret/raw exception data; unit coverage | complete for harness |
| Reproducibility metadata | host/runtime plus component/version/model/deployment manifest | complete for harness |
| OpenAI chat request/response | pinned-container CI lane committed | **first successful pinned run pending** |
| Streaming conformance | pinned-container + opt-in live test committed | **first successful pinned run pending** |
| Structured output/tool calling | pinned-container + capability-gated live tests committed | **first successful pinned run pending** |
| Controlled upstream unavailable behavior | pinned-container fault gate committed | **first successful pinned run pending** |
| Direct vs Bifrost latency/throughput | reproducible evidence harness committed | **representative measured evidence pending** |
| Direct vs LiteLLM vs Bifrost | optional three-target harness committed | **representative measured evidence pending** |
| SSRF/egress regression | source pin reviewed; isolated test matrix/runbook defined | **isolated runtime evidence pending** |
| Gateway routing/fallback conformance | explicit-target baseline and negative-policy procedure defined | **pending live evidence** |
| Restart and upgrade behavior | reproducible runbook defined | **pending live evidence** |
| Clustering/persistence | reviewed as optional operational surface | not required for first support; deeper evidence deferred |

## Decision

**Current outcome: `experimental_only`.**

Reasons:

1. Architecture fit is good: the existing generic OpenAI-compatible provider can target Bifrost without redefining canonical model identity or routing ownership.
2. The reviewed source is Apache-2.0 and the pinned release bundles a core version newer than the known 2026 SSRF fix.
3. Bifrost offers meaningful optional gateway functionality and is worth completing the evaluation rather than rejecting it now.
4. A high-severity SSRF history plus gateway/plugin/network surfaces makes runtime security evidence mandatory.
5. Current open upstream routing/governance issues argue for a deliberately narrow explicit-target support profile rather than treating Bifrost's own routing as equivalent to platform policy.
6. #859 explicitly requires representative performance, streaming/tool and operational measurements; those cannot be inferred from upstream claims or synthetic tests.

### Promotion to `supported_optional`

Promotion requires all of the following on the same reviewed/pinned release, or a newer separately reviewed revision:

- live OpenAI-compatible request/response conformance passes;
- native streaming conformance passes for the supported platform profile;
- supported tool-calling/structured-output cases pass or are explicitly capability-gated;
- direct vs Bifrost and direct vs LiteLLM performance evidence is recorded on representative local hardware/endpoints;
- the evidence proves the comparison paths use the intended equivalent downstream deployment;
- no provider-native identity escapes the canonical model boundary;
- synthetic-secret and real configuration diagnostics remain redacted;
- SSRF/egress regression passes, including redirect and DNS/address edge cases applicable to enabled URL-fetching features;
- unavailable-upstream, gateway restart and upgrade/restart behavior preserve canonical registry/router state;
- gateway routing/fallback configuration is proven not to bypass platform locality/capability/authorization restrictions;
- ordinary platform tests and startup remain valid with no Bifrost package/service installed.

Only after those gates pass should #799 surface Bifrost in **Advanced** setup as a `supported` optional gateway. It should not become the recommended/default model routing layer; `ModelRouter` remains the canonical authority.
