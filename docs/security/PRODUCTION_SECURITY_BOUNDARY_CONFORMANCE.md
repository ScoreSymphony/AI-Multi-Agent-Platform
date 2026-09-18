# Production security-boundary conformance

Issue #1233 closes the release-evidence gap between subsystem-specific security tests and one
platform-wide claim that supported side-effecting surfaces preserve the same canonical security
ownership model.

The executable inventory lives in
`ai_multi_agent_platform.conformance.security_boundaries`. The release
`platform-conformance` profile registers it as required scenario `SEC`. The matrix is an
**aggregator**, not a second policy engine: failures must be fixed in the owning subsystem.

## Maintained surface matrix

Every row records the same ten dimensions in code: authentication, authorization, approval,
tenant/project/workspace scope, secret/reference handling, audit evidence, cancellation/failure
behavior, fail-closed behavior, safe public error projection and provider-neutral canonical
authority.

| Surface | Canonical enforcement/evidence owner |
| --- | --- |
| Control Plane mutations | authenticated northbound boundary + `ControlPlaneAuthorizationBridge` + `AuthorizationGate` |
| Tool/Capability execution | `CapabilityInvoker` policy/governance boundary |
| MCP calls | canonical Capability invocation + shared egress enforcement |
| Browser/network actions | Browser capability policy + canonical egress/File boundaries |
| Terminal/process execution | Control Plane Terminal service + canonical Run/Workspace authorization |
| Repository/Git operations | Repository service/capability policy + Workspace boundary |
| Connector actions | Connector service through canonical Capability/authorization pipeline |
| Plugin actions | canonical Plugin Control Plane/lifecycle authorization |
| Application actions | canonical Application Control Plane + payload-bound approval |
| Local/remote Worker dispatch | authenticated Worker identity + canonical dispatch authorization/secret delivery |
| Marketplace install/update/uninstall | Control Plane authorization + exact resolved permission/action approval |
| Automation-triggered actions | automation identity + target action re-authorization + replay/stale-revision controls |
| Import/export | canonical portability validation/remapping + destination owner policy |
| Secret/configuration references | SecretReference/SecretProvider + canonical action authorization/redaction |
| Approval-gated operations | `AuthorizationGate` exact-action, exact-scope approval lifecycle |
| Verification/review transitions | canonical Verification/Completion authority + task/project reviewer scope |

The machine-readable matrix points only to maintained first-party tests. Contract tests reject a
surface with a missing dimension, duplicate/stale evidence node or a release profile that no longer
runs the complete matrix.

## Required attack/regression classes

The `SEC` evidence set includes representative regressions for:

- missing/expired authentication and revoked/scoped credentials;
- cross-project/project-session scope substitution;
- revoked/denied authorization on replay;
- missing approval and exact-action approval binding;
- stale approval after policy revision change;
- secret redaction, SecretReference portability and denied/unknown secret delivery;
- provider failure normalization;
- unavailable policy composition / fail-closed behavior;
- forged provider/external/Worker identifiers;
- replay/idempotency on Worker and automation side effects;
- remote Worker identity mismatch;
- cancellation/provider failure normalization;
- review/verification revision and scope integrity.

Focused provider/subsystem issues remain authoritative for their own implementation and live-host
claims. In particular, #1220, #1161, #1023, #730, #967 and #1026 are evidence inputs where
applicable; this matrix must not duplicate their policy authority or broaden their support claims.

## Threat-model reconciliation

The maintained threat model already names the relevant top-level trust boundaries: northbound
client -> Control Plane, identity -> policy/approval, model -> capability, capability -> provider /
Executor, Executor -> host/workspace/network, core -> plugin/adapter, Control Plane -> Worker,
external event -> Automation, connector/browser/repository content -> platform and
package/import/update -> installed/canonical state.

The #1233 pass makes two composed transitions explicit without creating new security authorities:

1. **Marketplace -> canonical owner handler**: Registry/Marketplace validation and Control Plane
   authorization must complete before the owning Plugin/Skill/Application/other handler may mutate
   state. The owner handler remains subordinate to existing adapter/plugin and supply-chain
   boundaries.
2. **Automation -> target mutation**: accepting an authenticated/deduplicated trigger is not
   authorization for the resulting Task or side effect. The generated target action re-enters its
   canonical authorization/approval boundary.

These are refinements of the existing trust-boundary graph, not new top-level trust domains. If a
future side-effect surface cannot be represented by the maintained matrix and threat-model
boundaries, the change must update both before release compatibility can be claimed.

## Release evidence

Run:

```text
platform-conformance \
  --profile release \
  --deployment-profile reference-single-node-release \
  --scenario SEC \
  --json-report conformance-security.json
```

A passing `SEC` result proves only the exact checked-out platform revision and maintained
reference/release profile. Provider-specific live-host guarantees remain scoped to their focused
evidence and support classification.
