# Application distribution conformance

This document defines the maintained application-distribution acceptance evidence introduced by issue #751. It is intentionally narrower than the platform-wide #46 conformance suite: #46 should consume this fixture set rather than reimplement provider, Control Plane, authorization, distributed-build or release-gate semantics.

The required suite is network-independent, uses no production credential and requires no paid external service.

## #751 acceptance fixtures

### GitHub publication negative paths

`tests/integration/application_distribution/test_issue751_github_conformance.py`

Proves with a controlled GitHub REST transport:

- same-provenance release reuse;
- same-name/same-digest asset idempotency;
- conflicting tag/source rejection without retagging;
- conflicting existing-release provenance rejection;
- same-name/different-digest asset rejection without replacement;
- repository/source/release lookup and release-create failures;
- manifest, checksum and application-asset upload failures;
- retry after an uncertain release-create response without a second logical release;
- public/private repository visibility semantics;
- recognizable fixture credential non-disclosure from provider outputs and representative errors.

### Versioned Control Plane and publication security

`tests/integration/application_distribution/test_issue751_control_plane_security.py`

Proves through the registered `application-releases` Control Plane surface:

- create replay with the canonical mutation idempotency key;
- create -> list/show -> build -> preview -> publish -> final show;
- canonical release identity and immutable source/Workspace binding;
- target OS/architecture/package metadata;
- Task/Run-backed Artifact/File admission;
- retained Worker/Node/runtime build provenance;
- target checksum and per-artifact download URL;
- immutable release URL plus optional `latest` URL;
- namespaced publisher external metadata;
- actor allowed to build but denied publication;
- Approval-required publication;
- exact Approval binding so a changed repository/configuration cannot reuse an Approval;
- side-effect-free publication preview;
- provider failure leaving canonical state un-published;
- preservation of canonical build provenance when publisher metadata is added.

### Generic CLI parity

`tests/integration/application_distribution/test_issue751_client_parity.py`

Proves the already-supported generic CLI extension surface can list/show `application-releases` without rewriting canonical target, artifact, download or external-metadata state. #751 does not require a dedicated application-distribution Web screen.

## Consumed #749 distributed-build evidence

#749 is complete, so distributed acceptance is no longer conditional. #751 consumes the real multi-worker fixture rather than duplicating it with a synthetic scheduler mock:

`tests/integration/application_distribution/test_remote_worker_multitarget.py`

It proves:

- one canonical ApplicationRelease identity survives Linux and Windows Worker dispatch;
- Worker/Node selection follows target OS/architecture requirements;
- Linux and Windows artifacts retain the correct target identity;
- an unsupported macOS/arm64 target is explicit and has no fabricated Task/Run;
- distinct eligible Workers are selected for distinct targets.

Additional remote-result fail-closed evidence remains in:

`tests/integration/application_distribution/test_remote_worker_result_evidence.py`

It rejects mismatched remote provenance, missing canonical artifacts and failed canonical File checksum verification.

The #751 Control Plane fixture additionally proves that admitted Worker/Node runtime provenance survives publication instead of being replaced by provider metadata.

## Consumed #750 release-gate evidence

#750 is complete. The canonical Verification/Evaluation release-gate fixtures remain authoritative and are consumed by #751/#46 rather than mocked away:

- `tests/integration/application_distribution/test_release_gate_provenance.py`
- `tests/integration/application_distribution/test_release_gate_verification_restart.py`
- `tests/integration/application_distribution/test_release_gate_evaluation_orchestration.py`
- `tests/integration/application_distribution/test_release_gate_package_smoke.py`

These fixtures prove that publication readiness is derived from current canonical gate evidence and that stale/non-passing evidence fails closed.

## Credential non-disclosure boundary

The acceptance suite uses recognizable fixture secrets. Required evidence is split deliberately by owning boundary:

- GitHub connector tests resolve a real fixture value from `LocalSecretProvider` and scan provider outputs/errors for that value;
- application build/secret tests owned by #748 prove resolved build-secret values do not persist into canonical state, manifests or recovery state;
- the #751 Control Plane fixture exposes only `SecretReference` metadata, never raw secret material;
- publisher external metadata remains provider-namespaced and does not contain raw connection credentials.

The GitHub reference provider does not emit a separate application-domain event or structured log containing the resolved token. If future provider instrumentation adds such surfaces, the recognizable-value scan must be extended to those emitted records rather than relying only on field-name assertions.

## #46 consumption

The platform-wide #46 suite should treat the following as the application-distribution acceptance bundle:

```text
tests/integration/application_distribution/test_issue751_github_conformance.py
tests/integration/application_distribution/test_issue751_control_plane_security.py
tests/integration/application_distribution/test_issue751_client_parity.py
tests/integration/application_distribution/test_remote_worker_multitarget.py
tests/integration/application_distribution/test_remote_worker_result_evidence.py
tests/integration/application_distribution/test_release_gate_provenance.py
tests/integration/application_distribution/test_release_gate_verification_restart.py
tests/integration/application_distribution/test_release_gate_evaluation_orchestration.py
tests/integration/application_distribution/test_release_gate_package_smoke.py
```

Running this bundle in normal CI must not contact GitHub or another paid/hosted build service and must not require a real production credential.
