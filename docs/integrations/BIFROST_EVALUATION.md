# Bifrost optional model-gateway evaluation

Status: `experimental_only`  
Issue: #859  
Reviewed: 2026-09-12

This document evaluates `maximhq/bifrost` as an **optional** self-hosted model gateway behind the platform-owned `ModelProvider`, `ModelRegistry`, and `ModelRouter` architecture. Bifrost is not baseline infrastructure and is not allowed to become the canonical routing authority.

## Decision

**Outcome: `experimental_only`.**

The evaluation is now sufficient to make a bounded decision:

- Bifrost fits the existing OpenAI-compatible `ModelProvider` boundary without introducing Bifrost-native canonical model identities.
- The reviewed source/license/security posture is acceptable for continued optional experimentation.
- Real same-backend CPU reference measurements show low gateway overhead for the tested workload and no canonical-identity failures.
- Restart and patch-upgrade behavior has been exercised successfully while the direct provider path remained available.
- Deployment-level SSRF regression evidence is positive for loopback/private/link-local/metadata/CGNAT/IPv6 transition classes and public-to-blocked redirect behavior.
- DNS-rebinding runtime evidence is still being completed after the first isolated harness run failed during Bifrost startup because the controlled resolver suppressed unrelated DNS traffic. The fixture now forwards non-target queries to Docker's resolver while preserving controlled answers for the rebinding hostname.
- Bifrost-native advanced routing/fallback/governance remains outside the approved support profile. The platform `ModelRouter` remains authoritative and the first admissible profile uses an explicit provider/model target with hidden cross-provider fallback disabled.

Therefore #799 must **not** present Bifrost as recommended/default infrastructure. If exposed before a later promotion, it may only be presented as an **Advanced / experimental** optional gateway. Promotion to `supported_optional` requires the remaining DNS-rebinding runtime gate plus a separately reviewed decision for any Bifrost-native routing/fallback features that would be enabled.

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
| Reviewed image digest | `sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245` |

The reviewed gateway exposes an OpenAI-compatible HTTP surface and broad provider/routing features. Platform support is capability-specific: upstream feature claims do not automatically imply provider/model fidelity.

## Architecture boundary

```text
Task / Agent requirements
        -> platform ModelRouter
        -> canonical ModelConfiguration
        -> selected ModelProvider
        -> OpenAI-compatible transport
        -> optional Bifrost gateway
        -> already-authorized downstream target
```

Bifrost is **not**:

- the canonical model inventory;
- the owner of platform routing policy;
- a replacement for `ModelRouter`;
- an authorization/approval authority;
- a canonical secret store;
- required for startup or direct-provider operation.

The existing `OpenAICompatibleModelProvider` is sufficient for the evaluated profile. A dedicated Bifrost adapter should only be introduced later if supported behavior needs Bifrost-specific health/capability/observability metadata that cannot be represented safely by the generic provider path.

Provider-native model identifiers remain deployment configuration. Canonical responses continue to expose the selected canonical model configuration ID; Bifrost aliases/provider names must not leak into `ModelResponse.model_ref`.

## Routing and fallback ownership

Bifrost can perform provider/key selection, retries, balancing, and fallback. Those features create a second routing layer and are therefore constrained by platform policy.

Binding precedence:

1. `ModelRouter` chooses the canonical candidate.
2. Platform locality/capability/authorization constraints are applied before gateway invocation.
3. The evaluated Bifrost profile targets one explicit provider/model path.
4. Hidden cross-provider fallback is disabled for the first admissible profile.
5. Any future gateway-side decision that changes the downstream target must be constrained to the already-permitted candidate set and surfaced through namespaced observability metadata.

Current upstream caveats reinforce that boundary:

- `maximhq/bifrost#3458` reports bare-model catalog resolution choosing a provider before governance/routing has necessarily made its final decision. The platform therefore does not accept bare-model catalog routing as equivalent to `ModelRouter` policy.
- `maximhq/bifrost#2351` reports custom-provider virtual-key/model-allowlist behavior on an earlier version. This review does not infer that the pinned release is affected or fixed; virtual-key governance remains unapproved until retested on the reviewed pin.

These caveats do not reject Bifrost itself. They reject treating Bifrost-native governance as a substitute for platform policy.

## Comparison with existing paths

| Path | Platform role | Status | Extra hop | Routing owner |
| --- | --- | --- | --- | --- |
| Direct provider | baseline/reference | supported | no | platform |
| LiteLLM | optional compatibility path from #11 | supported optional | proxy mode: yes | platform; advanced LiteLLM routing not baseline |
| Bifrost | optional HTTP gateway candidate | `experimental_only` | yes | platform |

## Real same-backend performance evidence

Workflow: `Bifrost evaluation performance`  
Successful run head: `237d8c83eb0f388d64afd6120569f1140bbede1e`  
Evidence artifact: `issue-859-real-gateway-performance`

Reference deployment:

- GitHub-hosted Ubuntu x64 CPU runner;
- AMD EPYC 7763, 4 vCPU visible to the job;
- `llama.cpp b10903`;
- `SmolLM2-135M-Instruct-IQ3_M.gguf`;
- Bifrost `transports/v2.1.1` / core `v1.8.6`;
- LiteLLM `1.99.0`;
- identical downstream `llama.cpp` instance for Direct, Bifrost, and LiteLLM;
- 24 measured operations per target, concurrency 2, 3 warmups;
- all targets preserved the same canonical model identity;
- 24/24 successful operations on each path and zero canonical errors.

Measured result:

| Path | p50 | p95 | p99 | Throughput | Ratio vs Direct |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 62.019 ms | 114.391 ms | 148.750 ms | 26.999 ops/s | 1.000 |
| Bifrost | 68.506 ms | 97.267 ms | 99.661 ms | 27.282 ops/s | 1.010491 |
| LiteLLM | 64.008 ms | 99.351 ms | 103.352 ms | 29.393 ops/s | 1.088675 |

Relative p50 overhead:

- Bifrost: `+6.487 ms` versus Direct;
- LiteLLM: `+1.989 ms` versus Direct.

The negative p95/p99 deltas for both gateways are not interpreted as gateways making inference intrinsically faster. This is a short CPU reference run where generation/runtime jitter dominates tails. The useful conclusion is narrower: under the same real local backend and workload, the tested Bifrost path did not introduce a material throughput collapse or canonical-model correctness regression.

This is reproducible **CPU reference evidence**, not a universal production/GPU latency guarantee.

## Compatibility evidence

The pinned real-container CI path uses `maximhq/bifrost:v2.1.1` against a controlled OpenAI-compatible fixture. Successful predecessor evidence demonstrates:

- ordinary chat completion;
- streaming;
- structured-output pass-through;
- tool-calling pass-through;
- canonical model identity preservation;
- controlled unavailable-upstream behavior;
- synthetic-secret diagnostics checks.

The synthetic fixture is compatibility/fault evidence only; it is not used as the representative performance result above.

## Security review

### Upstream advisory posture

The review includes `GHSA-w98g-5w9p-p3rc` / `CVE-2026-55245` (High), which affected Bifrost core versions before `v1.5.17` and concerned incomplete SSRF blocking in server-side URL fetching.

The reviewed transport bundles core `v1.8.6`, newer than the fixed range. That does not by itself establish deployment safety, so isolated runtime probes exercise the server-side `file_url` fetch path (`ResolveChatFileURLs` -> `FetchAndEncodeURL`).

### Deployment-level SSRF/egress evidence

Workflow: `Bifrost evaluation security`  
Successful run head: `237d8c83eb0f388d64afd6120569f1140bbede1e`  
Evidence artifact: `issue-859-bifrost-security-lifecycle`

The runtime matrix recorded `blocked_before_connection: true`, zero hits on the controlled blocked target, and one expected hit on the public redirect source. Covered classes:

- IPv4 loopback;
- RFC1918 `10/8`, `172.16/12`, `192.168/16`;
- IPv4 link-local / metadata range;
- CGNAT;
- IPv6 loopback;
- IPv4-mapped IPv6 loopback;
- IPv6 link-local;
- IPv6 unique-local;
- IPv6 site-local;
- 6to4 embedded loopback;
- NAT64 well-known embedded loopback;
- NAT64 local-use embedded loopback;
- Teredo;
- public-source redirect to loopback.

The remaining deployment-level edge case is DNS rebinding. The first dedicated rebinding job failed before the probe because the controlled DNS fixture answered only the test name and suppressed unrelated Bifrost DNS queries. That was a harness defect, not a positive or negative Bifrost security result. The fixture now forwards all non-target DNS packets to Docker's embedded resolver while retaining deterministic first-public/then-loopback answers for `issue859-rebind.test`.

Encoded/alternative host syntax is only applicable where the enabled Bifrost URL parser accepts the representation; unsupported syntaxes are not treated as successful security probes.

### Secrets and authority

Supported operation preserves these rules:

- provider credentials come from #34 secret/environment integration;
- plaintext credentials stay out of canonical configuration, evidence, normal logs, and diagnostics;
- virtual keys/governance do not grant platform authorization;
- #15 authorization/approval remains authoritative;
- plugin code is a separate trust boundary;
- benchmark evidence contains timings, aggregate errors, component/version/model metadata, and prompt fingerprint/length only, not raw prompts, responses, URLs, headers, or credentials.

## Lifecycle and absence evidence

Workflow: `Bifrost evaluation security`  
Evidence artifact: `issue-859-bifrost-lifecycle.json`

Verified:

- previous image: `v2.1.0`, digest `sha256:653b74a8410e5757375aa9a25ce21f90f4591fc11d65879451939b6172ce80ed`;
- reviewed image: `v2.1.1`, digest `sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245`;
- same file configuration/data root survived patch upgrade;
- `v2.1.0 -> v2.1.1` recovered successfully;
- restart of `v2.1.1` recovered successfully;
- the direct provider path remained available while Bifrost was stopped;
- no canonical state migration was required.

The repository also contains a deterministic regression that constructs `ModelRegistry` / `ModelRuntime` with a Direct provider only, proving that Bifrost can be entirely absent without changing the normal canonical routing path.

## Persistence and clustering

Bifrost offers persistence/cluster deployment surfaces, but they are not required for the first optional profile.

Platform rules:

- Bifrost persistence is not canonical model registry or Task/Run state;
- clustered Bifrost is not required for first support;
- removing Bifrost must only require repointing the affected provider configuration;
- canonical Agents/Tasks/model IDs must not migrate with gateway state.

## Reproducibility harness

The provider-neutral benchmark logic lives under:

- `ai_multi_agent_platform.benchmarking.model_gateway_evaluation`;
- `ai_multi_agent_platform.benchmarking.model_gateway_evidence`.

The concrete OpenAI-compatible composition is in:

- `ai_multi_agent_platform.adapters.model_gateway_evaluation_cli`.

The report records successful-operation p50/p95/p99, throughput, attempts/success/failure counts, canonical error categories, canonical model-identity conformance, platform/runtime metadata, component revisions, native model identifiers, and the common downstream deployment label.

Failed calls do not contribute to latency distributions or successful-operation throughput. Raw prompts are not serialized; only SHA-256 fingerprint and length are retained.

## Evidence / acceptance matrix

| Requirement | Evidence | State |
| --- | --- | --- |
| Exact upstream revision/version | `transports/v2.1.1`, `c193745...`, core `v1.8.6`, framework `v1.6.2` | complete |
| Apache-2.0 verification | reviewed at pinned commit | complete |
| Security advisory / patch posture | GHSA/CVE and upstream patch policy reviewed | complete |
| Canonical #10 architecture fit | generic OpenAI-compatible provider; platform router remains authority | complete |
| Bifrost absent from normal route | direct-only `ModelRegistry` / `ModelRuntime` regression | complete |
| Canonical identity preservation | deterministic tests + real three-target benchmark | complete |
| Secret/error redaction | unit coverage + synthetic gateway log check | complete |
| Reproducibility metadata | host/runtime/version/model/deployment evidence | complete |
| OpenAI chat | real pinned-container CI | complete |
| Streaming | real pinned-container CI | complete for evaluated profile |
| Structured output / tool calling | real pinned-container CI | complete for evaluated profile |
| Unavailable upstream behavior | controlled pinned-container fault gate | complete |
| Direct vs Bifrost performance | same real llama.cpp/SmolLM2 backend | complete CPU reference |
| Direct vs LiteLLM vs Bifrost | same real llama.cpp/SmolLM2 backend | complete CPU reference |
| SSRF address / redirect matrix | isolated deployment-level probe | complete for listed classes |
| DNS rebinding | dedicated isolated runtime harness | rerun pending after DNS forwarding fix |
| Restart / patch upgrade | `v2.1.0 -> v2.1.1`, restart, direct path during outage | complete |
| Advanced Bifrost routing/fallback | intentionally not admitted to evaluated support profile | deferred / experimental only |
| Clustering/persistence | optional operational surface | deferred; not required for first profile |

## Promotion criteria

Promotion from `experimental_only` to `supported_optional` requires:

1. the DNS-rebinding runtime gate to pass on the reviewed release (or a newer separately reviewed pin);
2. ordinary platform CI/required checks to be green on the final PR head;
3. explicit provider/model targeting to remain the default supported profile;
4. any Bifrost-native routing/fallback/governance feature proposed for enablement to receive separate policy-conformance evidence proving it cannot escape platform locality/capability/authorization constraints;
5. the gateway to remain removable without canonical state migration.

Even after promotion, #799 should surface Bifrost only as an **Advanced optional gateway**. `ModelRouter` remains the canonical routing and policy authority.
