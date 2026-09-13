# Bifrost live evidence runbook

Issue: #859  
Evaluation status: `experimental_only`  
Reviewed target: `maximhq/bifrost` `transports/v2.1.1` at `c193745d2a713e9f58f021d43e138df5eb7e038a` (bundled core `v1.8.6`)

This runbook defines the live evidence required to decide whether Bifrost may be promoted from `experimental_only` to `supported_optional`. It is intentionally reproducible and does not treat successful unit tests or upstream feature claims as runtime evidence.

## Safety and architecture invariants

The live campaign must preserve these rules:

- the platform `ModelRouter` remains the canonical routing/policy authority;
- use one explicitly selected downstream model/provider target for the first support campaign;
- disable or avoid hidden cross-provider fallbacks, weighted balancing and bare-model catalog routing during the baseline comparison;
- direct and Bifrost paths must reach the same downstream model/runtime on the same host or otherwise record why the deployment is equivalent;
- no real production credential is required; use synthetic or dedicated test credentials;
- do not probe real cloud metadata services or unrelated private hosts during SSRF testing;
- Bifrost remains optional: stopping/removing it must not break direct-provider startup or canonical `ModelRouter` operation.

## 1. Record the exact test environment

Record before testing:

- platform commit SHA;
- Bifrost tag, commit and container image digest or binary checksum;
- bundled Bifrost core version;
- direct inference runtime name/version and model identifier;
- optional LiteLLM version/image digest;
- OS, CPU, RAM and accelerator/runtime details;
- whether all comparison paths use the same downstream model instance;
- Bifrost routing/fallback/governance configuration relevant to the request path.

The benchmark CLI writes platform/host metadata plus secret-free per-target version/revision/model metadata into the JSON artifact. Endpoint URLs, headers and credentials are deliberately excluded. The provider-neutral benchmark logic lives under `ai_multi_agent_platform.benchmarking`; the executable OpenAI-compatible composition entry point lives under `ai_multi_agent_platform.adapters` so the core benchmark layer does not import concrete adapters.

## 2. Establish the direct baseline

Verify the downstream OpenAI-compatible endpoint directly before starting gateway measurements.

Required baseline behavior:

- ordinary chat completion succeeds;
- canonical model identity is preserved by the platform provider adapter;
- streaming succeeds when supported by the target model/runtime;
- structured output and tool calling are tested only when the target declares those capabilities;
- the same model/runtime configuration is retained for gateway measurements.

A failing or changing downstream baseline invalidates the direct-vs-gateway comparison.

## 3. Configure the pinned Bifrost target

Use the reviewed Bifrost release or a newer revision that has received a separate provenance/security review.

For the first support campaign:

- configure exactly the intended downstream provider/model path;
- prefer an explicit provider/model identifier over a bare model name;
- keep hidden cross-provider fallbacks/load balancing disabled;
- restrict gateway/admin network exposure to the test environment;
- keep provider credentials outside committed configuration;
- record whether virtual-key/governance features are enabled.

Do not assume that Bifrost routing/governance behavior is equivalent to platform policy. The campaign must prove the configured path cannot escape the platform-approved target set.

## 4. Run OpenAI-compatible conformance

Configure the live environment variables:

```text
BIFROST_EVAL_BIFROST_BASE_URL
BIFROST_EVAL_BIFROST_MODEL
BIFROST_EVAL_BIFROST_API_KEY_ENV   # optional; value is an env-var name
BIFROST_EVAL_STRUCTURED_OUTPUT=1   # only when the model declares JSON-mode support
BIFROST_EVAL_TOOL_CALLING=1        # only when the model declares tool support
```

Run:

```bash
pytest -q tests/contract/models/test_bifrost_live_conformance.py
```

Required evidence:

- chat request/response succeeds;
- streaming emits canonical text-delta/completed events and preserves canonical model identity;
- structured output passes when declared supported, otherwise remains capability-gated;
- tool calling passes when declared supported, otherwise remains capability-gated;
- no Bifrost/provider-native model identifier replaces the canonical `ModelResponse.model_ref`.

## 5. Run direct vs Bifrost performance evidence

Use the same downstream deployment for both paths and record a stable deployment label describing that equivalence.

Example:

```bash
python -m ai_multi_agent_platform.adapters.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --downstream-deployment-label same-vllm-qwen-local-instance \
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

The Bifrost version/revision defaults in this evaluator are the reviewed `transports/v2.1.1` / core `v1.8.6` / `c193745...` pin. Override them only when the actual live target differs and that revision has been reviewed.

Record at least:

- p50/p95/p99 end-to-end latency for successful operations;
- attempted/successful/failed operations;
- successful-operation throughput;
- canonical error categories;
- canonical identity preservation;
- host/runtime metadata and exact component/model metadata emitted by the evidence manifest.

Failed operations are reported separately and are deliberately excluded from successful latency distributions and successful-operation throughput. A failed-only target therefore has no synthetic latency delta.

Do not invent a universal acceptable latency threshold. Interpret overhead in the context of the tested hardware, downstream model and operational benefit.

## 6. Compare with LiteLLM

When LiteLLM is available, repeat the exact workload against the same downstream model/runtime:

```bash
python -m ai_multi_agent_platform.adapters.model_gateway_evaluation_cli \
  --canonical-model-id model-local-eval \
  --downstream-deployment-label same-vllm-qwen-local-instance \
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

Use identical prompts, operation count, concurrency and warmup settings. If one path uses a materially different downstream deployment, the result is not a gateway-overhead comparison and must be labelled accordingly.

## 7. Synthetic-secret/redaction check

Use a unique synthetic marker as the test credential, for example through a dedicated environment variable. Exercise success and failure paths, then inspect:

- benchmark JSON;
- platform logs;
- Bifrost logs/diagnostics included in the test evidence;
- exceptions captured by the test harness.

The marker must not appear in the platform benchmark artifact or normal platform diagnostics. Raw prompts, responses and exception messages are not part of the benchmark report by design; the workload prompt is represented only by its SHA-256 fingerprint and length.

## 8. Upstream unavailable and restart tests

Exercise at least these states:

1. **Downstream model unavailable while Bifrost remains up**
   - request fails through a canonical error category;
   - no provider-native identity becomes canonical;
   - model registry state is not corrupted.
2. **Downstream model restored**
   - the same canonical model configuration works again without recreation.
3. **Bifrost stopped**
   - Bifrost-backed provider becomes unavailable/fails canonically;
   - the direct provider path remains usable;
   - ordinary platform startup and routing do not require Bifrost.
4. **Bifrost restarted with the same reviewed configuration**
   - requests recover without rewriting canonical agents/tasks/model IDs.
5. **Upgrade/restart**
   - review provenance/security of the target revision first;
   - restart on the new version;
   - rerun conformance and fault checks;
   - verify no migration of canonical platform model identity/state is required.

Record timestamps/version identifiers and the observed canonical error categories. Do not record provider secrets.

## 9. SSRF/egress regression campaign

Run this only in an isolated test network with controlled listener/DNS fixtures. Do not target real metadata endpoints or unrelated private infrastructure.

Exercise URL-fetching features that are actually enabled in the Bifrost profile against controlled representatives of:

- IPv4 loopback;
- RFC1918/private IPv4;
- IPv4 link-local / synthetic metadata-range listener;
- CGNAT (`100.64.0.0/10`);
- IPv6 loopback/link-local/site-local;
- IPv4-mapped IPv6;
- 6to4 (`2002::/16`);
- NAT64 (`64:ff9b::/96`);
- Teredo (`2001:0000::/32`);
- public-to-blocked redirects;
- DNS rebinding/resolution-time address changes;
- encoded/alternative host representations applicable to the enabled feature.

Passing means the request is rejected before a connection reaches the controlled blocked target and that redirects/re-resolution cannot cross the egress boundary. Record Bifrost version/commit and enabled feature/configuration for every result.

The reviewed core already contains source-level regressions for these address classes and per-dial DNS validation. Those tests are useful evidence about the pinned source, but they do not replace this isolated deployment-level campaign.

## 10. Routing/fallback policy test

The first supported profile must prove that Bifrost cannot silently escape the target already authorized by the platform.

Test at least:

- explicit provider/model target;
- disallowed alternative provider/model present in Bifrost configuration;
- downstream failure with gateway fallback disabled;
- if fallback is later proposed, an allowlisted equivalent target and a deliberately disallowed target.

The disallowed target must never receive a request. Any materially different gateway-side target selection must be observable in namespaced diagnostics before such routing can be considered supported.

## 11. Decision gate

Promote to `supported_optional` only when all required live evidence is attached/reviewed and ordinary repository checks pass. Otherwise retain `experimental_only` or move to `reject/defer` if security, policy ownership, compatibility or operational cost is unacceptable.

Even after promotion:

- Bifrost remains optional;
- it is not the default/recommended platform router;
- the platform `ModelRouter` remains authoritative;
- #799 may expose it only as an Advanced optional component after #799's own acceptance criteria are satisfied.
