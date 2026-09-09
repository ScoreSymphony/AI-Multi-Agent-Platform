# Issue #591 integration handoff

This document records the intended final integration of the prepared #591 implementation. The
feature branch deliberately avoids broad edits to central single-node composition while other active
branches are being developed in parallel. The shared integration branch should wire these prepared
components once, resolve cross-branch conflicts once, and only then run the unified validation suite.

## Prepared branch

- Branch: `feat/591-egress-hardening`
- Draft PR: `#652`
- Issue: `#591`
- Intent: merge/cherry-pick into the later active-branches integration branch; do not treat the
  standalone draft PR as the final validated deployment state.

## Canonical pieces already implemented

The branch contains the policy/domain implementation rather than placeholders:

- canonical `DataClassification` ordering and monotonic derivation rules;
- provider-neutral `EgressTarget`, `EgressProfile`, `EgressRequest`, `EgressDecision` and audit
  contracts;
- fail-closed `CanonicalEgressPolicy` including paid-external, unknown-cost, unknown-posture and
  unverified-profile policy;
- project-aware durable `JsonEgressProfileRepository` with immutable contiguous revision history;
- authorized `EgressProfileService` lifecycle and explicit verification transition;
- repository-backed profile resolution without a second provider registry;
- optional #15 Approval bridge bound to exact payload digest, destination, action/scope, policy and
  profile revision;
- egress-aware model candidate filtering plus provider-call defense in depth;
- capability/MCP/browser transport enforcement, including read-only network egress detection;
- Connector enforcement and monotonic result/resource/event classification inheritance;
- Context Bundle and file/artifact export gates;
- safe Control Plane profile management, classification vocabulary view and digest-only policy
  inspection;
- #79 EgressProfile portability with source trust/sensitive opt-in removal and arbitrary metadata
  stripping;
- #16 `EgressTelemetryAuditSink` for value-free timeline/metric/log projection;
- prepared regression tests for policy, routing, Approval, capability/browser/MCP, Connector,
  persistence/portability and telemetry behavior.

## One-runtime integration rule

The final deployment must construct **one** durable egress runtime and share its gate. Do not create
one EgressGate per provider boundary.

Recommended single-node composition:

```python
from ai_multi_agent_platform.observability import EgressTelemetryAuditSink
from ai_multi_agent_platform.security import build_durable_egress_runtime
from ai_multi_agent_platform.deployment import EgressDeploymentBindings

runtime = build_durable_egress_runtime(
    config.database_dir / "egress-profiles.json",
    authorization=base.authorization,
    approval_gate=base.approval_gate,
    audit_sink=EgressTelemetryAuditSink(base.telemetry),
)
egress = EgressDeploymentBindings(runtime)
```

Use the deployment's actual authorization provider/actor resolver names after resolving integration
branch changes; do not create duplicate security services merely to match this example.

## Required final wiring in the integration branch

1. **Model execution**
   - Ensure the production `ModelRuntime` is constructed with `egress.runtime.gate` (or through
     `egress.model_runtime(...)`).
   - Preserve the egress-aware preselection hook so incompatible remote candidates are excluded
     before deterministic model selection.
   - Keep the second enforcement immediately before provider invocation.

2. **Agent capability execution**
   - Construct the production `AgentCapabilityTurn` with `egress.capability_invoker(...)`.
   - Preserve the canonical capability binding/governance/observer hooks already used by the final
     integration branch.
   - Browser `browser.network.read`, MCP/external and distributed capability paths must all pass the
     same gate.

3. **Connectors**
   - Replace direct production construction of `EgressConnectorService` with
     `egress.connector_service(...)` so it shares the durable gate.
   - Keep the existing AuthorizationGate and canonical ConnectorRegistry/Repository; #591 must not
     take ownership of connector identity.

4. **Context Bundles**
   - Use `egress.context_exporter()` for any path that renders/releases an immutable ContextBundle to
     an outbound destination.
   - Retain `effective_data_classification` in Control Plane Bundle/Run projections.

5. **Files and artifacts**
   - Use `egress.file_exporter(file_provider)` for outbound file/artifact release paths rather than
     reading bytes first and evaluating policy afterwards.

6. **Control Plane**
   - Call `egress.register_control_plane(base.control_plane)` once. It registers:
     - canonical classification vocabulary;
     - `egress-profiles` lifecycle/read surface;
     - digest-only `egress-policy.evaluate` inspection.
   - The inspection path must remain policy-only: no Approval mutation and no execution audit.

7. **Portability**
   - Call `register_egress_profile_portability(...)` against the final shared serializer,
     export-source and mutation registries.
   - Supply an explicit destination `OwnerRef` for imports.
   - Imported profiles remain `unverified`; re-verification belongs to the destination deployment.

8. **Observability**
   - Pass one `EgressTelemetryAuditSink(base.telemetry)` to the durable runtime.
   - Preserve digest-only/value-free evidence. Never add raw prompt text, connector arguments, file
     bytes or secret values to `EgressAuditEvent.audit_metadata`.
   - When reconciling the final gate implementation, populate `EgressAuditEvent.project_id` from
     `request.context.project_id` so project scope is retained in #16 telemetry.

## Compatibility / conflict rules

During integration prefer the final versions of shared central files from the integration branch,
then re-apply the #591 seams rather than replacing newer architecture wholesale. In particular:

- `deployment/single_node.py` / `deployment/durable_connectors.py`: keep newer services and insert the
  one-runtime wiring;
- `observability/__init__.py`: retain all exports from concurrent branches and add
  `EgressTelemetryAuditSink`;
- `portability/__init__.py`: retain all concurrent codecs/handlers and add the EgressProfile exports;
- `security/__init__.py`: retain concurrent security exports and the durable egress public API;
- `context/*`: keep newer #590/context work while preserving canonical effective classification and
  outbound exporter enforcement;
- `models/router.py` / `models/runtime.py`: do not drop newer routing-profile logic when preserving
  the candidate-policy hook.

## Acceptance mapping after wiring

| #591 criterion | Prepared implementation | Final integration action |
| --- | --- | --- |
| canonical effective classification | contracts + data/context propagation | preserve merged domain fields |
| versioned provider egress profiles | durable repository/service | instantiate one repository |
| model routing exclusion | router/runtime hooks | inject shared gate |
| Context Bundle compatibility | Context exporter | use exporter on outbound path |
| capability/MCP/Connector/browser hook | egress invoker/service | inject shared gate/turn |
| fallback cannot widen disclosure | candidate policy + explicit-route failure | preserve routing hook |
| Approval exact/stale binding | #15 bridge | supply approval gate only if enabled |
| secrets remain #34-owned | reference-only policy | preserve SecretProvider boundary |
| paid external denied by default | canonical v2 policy | keep default flags false |
| value-free audit | EgressAuditEvent + telemetry sink | inject telemetry sink |

## Validation deferred to the integration branch

No standalone validation result should be inferred from this handoff. After all active branches are
combined, run the repository's normal formatting/lint/type/test/CI/conformance suite against the
**combined head**, fix integration regressions there, and then review the #591 acceptance matrix
against the actual production composition.

Do not close #591 or merge the final integration result while relevant required checks are failing.
