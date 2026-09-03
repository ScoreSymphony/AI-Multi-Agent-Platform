# Hermes orchestrator adapter

Issue #8 integrates Hermes Agent as the first production-oriented **optional orchestrator adapter**. Hermes remains a separately deployed service and does not become the source of truth for Tasks, Runs, Agents, Teams, Plans, Steps, approvals, model assignments, capabilities, artifacts, results or lifecycle state.

## Integration boundary

The platform follows this direction:

```text
Canonical Task / Agent / Team
        |
        v
platform-owned Hermes adapter
        |
        v
Hermes API-server private run/session/tool/model representation
```

The reverse direction is not canonicalization. A Hermes run/session ID never becomes a platform Task, Run, Agent, Team, Plan or Step ID.

The baseline transport is Hermes' documented OpenAI-compatible API server. The compatibility target is:

- upstream: `NousResearch/hermes-agent`
- pinned commit: `63279301bcbdc185c1b07b98a9312eb0c862f26d`
- verified license: MIT
- transport: HTTP JSON against the API-server `/v1/runs` surface

No Hermes Python package or source tree is required by platform core or reference tests.

## Configuration

`HermesAdapterConfig` is disabled by default. `config/hermes.example.json` shows the configuration shape.

Important fields:

- `enabled`: explicit opt-in;
- `base_url`: separately deployed Hermes API-server endpoint;
- `api_key_env`: environment/secret reference only; secret values are not configuration metadata;
- `pinned_revision`: expected upstream compatibility target;
- request/poll/plan timeouts;
- optional Hermes multiplex `profile`;
- `model_bridge`: canonical model-configuration ID -> Hermes model selector;
- `capability_bridge`: canonical Capability ID -> Hermes tool selector.

The baseline runtime mode is deliberately **external API server only**. `base_url` and optional `profile` select that endpoint; there is no hidden in-process Hermes mode. A future runtime mode must be added as an explicit adapter capability/configuration choice rather than inferred from imports or environment state.

Retry behavior is also explicit: the adapter performs **no hidden automatic request retry loop**. HTTP 429, HTTP 5xx and connection failures are normalized as canonical retryable errors, after which platform/caller retry policy decides whether another canonical operation attempt is appropriate. `/v1/runs` admission carries the canonical idempotency key so a caller-authorized retry can reuse Hermes' idempotency boundary rather than inventing a second lifecycle.

Supported behavior is advertised through `ProviderDescriptor`: the adapter publishes its canonical operations/capabilities, availability/health, transport mode and expected upstream revision. Features not listed there or in the supported-behavior section below are not implied by the presence of a Hermes deployment.

Baseline diagnostics are intentionally bounded: the adapter propagates `X-Correlation-Id`, exposes health, and preserves backend identity/status details only in the `hermes` adapter-metadata namespace. Secret values are resolved only when sending a request and are not copied into canonical metadata. The baseline adapter does not log request/response payloads or credentials. Cross-layer logs, traces and metrics belong to the platform observability work in #16 rather than to a Hermes-private logging model.

`pinned_revision` is the **expected compatibility target**, not a claim that an arbitrary remote service has cryptographically attested its running source revision. The repository treats a revision as verified only after the pinned compatibility fixture and full CI pass for that revision. Pointing `base_url` at another Hermes revision is therefore an explicit unverified deployment choice until the pin, provenance record and compatibility tests are updated together.

The adapter never requires a paid Hermes/model service. A locally/self-hosted Hermes instance and local/self-hosted model endpoint are valid targets.

## Canonical Task -> Hermes run

`HermesOrchestrator` implements the platform-owned `Orchestrator` contract.

A canonical `PlanRequest` maps as follows:

| Canonical field | Hermes/API mapping | Ownership |
| --- | --- | --- |
| `task_id` | included as trace context in the planning input; never used as Hermes `run_id` | platform |
| `context.correlation_id` | planning trace text + `X-Correlation-Id` diagnostic header | platform |
| `context.control.idempotency_key` | Hermes `Idempotency-Key` header | platform intent; Hermes stores private replay state |
| `context.control.timeout_seconds` | platform-side orchestration deadline | platform |
| `objective` | `/v1/runs` `input` | platform intent |
| planner contract | `/v1/runs` `instructions` | platform adapter |
| Hermes returned `run_id` | `AdapterMetadata(namespace="hermes")` only | Hermes/private |
| Hermes session ID | adapter-private reconciliation data only | Hermes/private |

The planner instruction explicitly asks Hermes for proposal-local step keys rather than canonical Plan/Step IDs. `PlanResponse` remains provider-neutral and the platform remains responsible for creating canonical Plan/Step identities later.

## Hermes output -> canonical PlanResponse

The baseline adapter requires one JSON object:

```json
{
  "summary": "...",
  "steps": [
    {
      "key": "proposal-local-key",
      "title": "...",
      "objective": "...",
      "depends_on": []
    }
  ]
}
```

The adapter validates:

- a non-blank summary;
- an array of step objects;
- non-blank proposal-local `key` and `title`;
- string objectives;
- string dependency keys;
- the canonical `PlanResponse` graph invariants (unique keys and known dependencies).

Malformed or structurally invalid Hermes output becomes `INVALID_PROVIDER_RESPONSE`. Hermes-private response types never escape the adapter.

## Exact Agent revision mapping

`HermesAgentMapper` implements the #33 `AgentOrchestratorMapper` seam. It consumes the exact `AgentExecutionSpec` that was already resolved and pinned by `AgentRuntime`.

The adapter-private mapping records:

- exact canonical Agent ID + revision;
- canonical Agent role/name/description and instruction layers;
- exact selected canonical model configuration/provider;
- exact canonical Capability IDs + resolved versions;
- canonical memory/knowledge declarations and policy references as declarations;
- Task and Project/Workspace context;
- exact Team ID + revision when present.

The resulting `OrchestratorMapping.runtime_ref` is explicitly namespaced as a Hermes **mapping** reference. It is not a canonical Run ID and is not represented as a Hermes-native run until Hermes actually admits work.

## Model bridge

Hermes may accept a provider/model selector, but provider-native names are not canonical model identities.

When `AgentExecutionSpec.selected_model_config_id` is set, `HermesAgentMapper` requires an explicit entry in `model_bridge`. Missing mappings fail with `INVALID_CONFIGURATION`; the adapter never silently selects a different model.

This keeps model ownership in #10:

```text
canonical ModelRegistry / ModelRouter
        -> selected model config ID
        -> Hermes model_bridge
        -> Hermes-private model selector
```

## Capability/tool bridge

Canonical Capability IDs and their resolved versions are pinned before Hermes mapping. For every requested capability, `HermesAgentMapper` requires an explicit `capability_bridge` target.

Missing mappings fail with `UNSUPPORTED_CAPABILITY`. This prevents Hermes' own tool catalog from silently expanding an Agent's platform-authorized capability set.

The bridge entry is a translation target, not approval authority. Canonical permission/approval policy remains platform-owned.

## Team mapping

The exact #33 Team revision maps into adapter-private coordination metadata:

- Team ID/revision/name;
- exact member Agent IDs/revisions and roles;
- required/optional membership;
- delegation relationships;
- leader Agent ID;
- shared Capability IDs/resource references;
- `max_parallel_agents`;
- `max_steps`;
- unavailable-member policy.

Hermes does not define the canonical Team. The baseline adapter does **not** claim that every Team field has a first-class upstream Hermes API primitive. In particular, shared memory/resources, platform authorization and verification semantics remain platform services. A Hermes runtime integration may use its private delegation/subagent mechanisms only behind this mapping and may not weaken the pinned Team policy.

## Plan/Step mapping

Hermes creates a plan **proposal** only. Proposal-local keys map into `PlanStepProposal`. Canonical Plan/Step IDs are allocated by the platform kernel/application layer, never by Hermes.

If Hermes later exposes richer planning structures, they must still normalize into `PlanResponse` rather than extend canonical Plan/Step contracts with Hermes-only fields.

## Cancellation and reconciliation

Hermes' documented run endpoints are used only as adapter operations:

- `GET /v1/runs/{id}` -> `reconcile_external_run()`;
- `POST /v1/runs/{id}/stop` -> `cancel_external_run()`;
- an asyncio cancellation of canonical `plan()` triggers a best-effort Hermes stop before cancellation propagates;
- a platform-side planning timeout triggers a best-effort stop and canonical `TIMEOUT`.

Reconciliation never overwrites canonical Task/Run lifecycle. It returns `HermesRunSnapshot`, an adapter-private value object.

## Approval boundary

Hermes `/v1/runs` may enter `waiting_for_approval`. The baseline planning adapter **never auto-approves it**. Because canonical approval authority belongs to the platform, a planning run that asks Hermes for approval fails closed with a canonical `FORBIDDEN` result until a future explicit platform approval bridge is introduced.

The existence of Hermes' `/v1/runs/{id}/approval` endpoint therefore does not make Hermes an approval authority.

## Error mapping

HTTP and run outcomes are normalized before leaving the adapter:

| Hermes/transport outcome | Canonical category |
| --- | --- |
| HTTP 400 | `INVALID_REQUEST` |
| HTTP 401 | `UNAUTHORIZED` |
| HTTP 403 | `FORBIDDEN` |
| HTTP 404 | `NOT_FOUND` |
| HTTP 409 | `CONFLICT` |
| HTTP 429 | `RATE_LIMITED` (retryable) |
| HTTP 5xx / connection failure | `UNAVAILABLE` (retryable) |
| request/planning timeout | `TIMEOUT` |
| run cancelled/interrupted | `CANCELLED` |
| invalid Hermes plan payload | `INVALID_PROVIDER_RESPONSE` |
| Hermes run failure | `BACKEND_ERROR` |

Backend diagnostic identity is isolated under the `hermes` adapter-metadata namespace.

## Supported and intentionally unsupported behavior

Supported in the baseline adapter:

- canonical `Orchestrator.plan()`;
- `/v1/runs` admission and idempotency;
- polling/reconciliation;
- cancellation/stop;
- exact Agent revision mapping;
- Team structural mapping;
- explicit model/capability bridges;
- health probing;
- multiplex profile prefix;
- canonical error normalization.

Not promoted into the canonical baseline:

- Hermes session history as canonical Task history;
- Hermes memory as canonical platform memory;
- Hermes tool allowlists as canonical authorization;
- Hermes approval state as canonical approval state;
- Hermes subagent IDs as canonical Agent IDs;
- Hermes run/session IDs as canonical Run IDs;
- direct dashboard/TUI private APIs;
- in-process `AIAgent` imports.

These are deliberate isolation decisions, not missing canonical fields.

## Replaceability and disabled path

Hermes is optional. With `enabled=false`:

- the adapter advertises itself unavailable;
- attempts to use it fail canonically before network work;
- reference orchestrator, fake adapters, core imports and kernel tests require no Hermes installation;
- removing `adapters.hermes` does not require migration of canonical Task/Run/Agent/Team state.

The same `PlanRequest` can be passed to the reference/fake orchestrator or `HermesOrchestrator`, and the same `AgentExecutionSpec` can be mapped by `ReferenceOrchestratorMapper` or `HermesAgentMapper` without changing canonical identities.

## Upstream update procedure

Hermes is pinned by commit, not tracked implicitly from `main`.

For an update:

1. compare the proposed revision with `63279301bcbdc185c1b07b98a9312eb0c862f26d`;
2. re-verify the license and programmatic-integration/API-server contracts;
3. review `/v1/runs`, status, stop, approval, capability and health behavior;
4. review security/auth/profile changes;
5. update `upstream/hermes-agent.yaml`, this document and `docs/UPSTREAMS.md`;
6. run adapter unit/contract tests plus the real pinned Hermes integration fixture;
7. run full repository CI;
8. require an ADR before any change that would move lifecycle/domain ownership into Hermes.

## Exit strategy

Remove the Hermes configuration, adapter and external service. Canonical Tasks, Runs, Agents, Teams, Plans, model configurations, capabilities and event history remain valid and can be routed through another `Orchestrator`/`AgentOrchestratorMapper` implementation.