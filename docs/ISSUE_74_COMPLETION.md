# Issue #74 completion matrix — replaceable browser and web capability

This document maps the implementation for issue #74 to its acceptance criteria and required tests.
It describes the platform boundary implemented by PR #183; it does not make any concrete browser
engine canonical.

## Delivered architecture

| Deliverable | Implementation |
| --- | --- |
| Browser capability schema/contracts | `browser/models.py`, `browser/contracts.py`, canonical `CapabilitySpec` registrations in `browser/reference.py` |
| Replaceable `BrowserProvider` boundary | `BrowserProvider` extends the existing capability-provider seam; `BoundBrowserProvider` decorates any implementation without changing task requests |
| Canonical browser session/context | `BrowserSessionRef` / `BrowserSessionScope`, with owner/project/task/run/agent scope fields, timestamps, privacy classification, allowed domains and namespaced adapter metadata |
| Reference browser adapter | `StdlibBrowserProvider`, self-hosted and Python-standard-library based |
| Capability-registry registration | canonical browser capability registrations resolve through `CapabilityRegistry` like other tools |
| Upload/download file bridge | uploads consume authorized canonical `file_*` refs through `FileProvider`; downloads create canonical `file_*` refs, link a canonical `artifact_*` through the refined #13 file/artifact seam, and record SHA-256, content type and provenance |
| Authorization/network policy | capability safety/side-effect classifications, canonical policy/approval pipeline, `BrowserNetworkPolicyHook`, SSRF/private-network and redirect checks |
| Worker-placement hooks | `BrowserPlacement` / `BoundBrowserProvider` attach node, worker, priority and provider-private worker labels at registration level |
| Observability/evidence | `browser.operation` adapter metadata plus canonical `InvocationRecord` propagation; downloads use `result_ref`, `artifact_refs` and `evidence_refs` |
| Documentation/examples | `docs/BROWSER_CAPABILITY.md` |

## Canonical capability set

The reference adapter publishes:

- `browser.navigate`
- `browser.extract`
- `browser.follow_link`
- `browser.submit_form`
- `browser.download`
- `browser.close_session`

The canonical contract does not require JavaScript, screenshots or a particular DOM/browser
engine. Providers advertise those features through `BrowserProviderFeatures`; the reference
adapter reports JavaScript/screenshots/interactive mode as unsupported. A richer provider may
implement the same canonical capability IDs and add supported feature metadata without changing
agent/task contracts.

## Acceptance criteria

- [x] **Browser functionality is exposed as canonical capabilities.** Browser actions are normal
  `CapabilitySpec` registrations and execute through `CapabilityInvoker`.
- [x] **At least one replaceable reference adapter can open/navigate/extract content.**
  `StdlibBrowserProvider` supports navigation, text/link extraction, link following, forms and
  reusable isolated sessions; provider replacement is tested.
- [x] **File downloads become canonical File/Artifact resources with provenance.** The browser
  creates a canonical `file_*` through `FileProvider`, generates a canonical `artifact_*`, links
  the artifact to the file through the refined #13 `link_artifact` seam, records source URL,
  content type, SHA-256, timestamp, provenance and trust classification, and returns the file and
  artifact through canonical result/evidence fields. No browser-provider-private artifact ID or
  parallel artifact store is introduced.
- [x] **Uploads use authorized canonical file references.** `browser.submit_form` requires both
  `browser.external.submit` and `file.read`; uploads accept only `file_*` references and read bytes
  through the injected `FileProvider`. Host/temp paths are not accepted as canonical input.
- [x] **Provider-private browser/session IDs do not leak into canonical APIs.** Canonical sessions
  use `browser_session_*`; cookies and native engine/session identifiers stay provider-private.
- [x] **Browser capability can be disabled/removed without breaking core tools.** Absence/removal
  of a browser provider yields the normal unsupported-capability result and does not alter the
  core registry/tool pipeline.
- [x] **Network/domain policy hooks exist.** The default policy supports scheme restrictions,
  allow/deny domains, URL-credential rejection, private/loopback/link-local/reserved address
  blocking, response-size limits and redirect re-checks.
- [x] **Sensitive/external-side-effect operations pass authorization/approval gates.** Form
  submission is a restricted external side effect and is denied before network mutation when the
  canonical policy hook rejects it; downloads are restricted local writes.
- [x] **Browser work can later move between local/remote workers without changing task logic.**
  Placement is registration metadata (`node_id`, `worker_id`, priority and worker labels), not a
  browser task-schema field. The canonical capability definition remains provider-independent.

## Required-test mapping

| Required test | Coverage |
| --- | --- |
| open/navigate page | `test_navigation_extract_follow_and_trace_preservation` |
| extract content | `test_navigation_extract_follow_and_trace_preservation` |
| multi-step interaction fixture | navigation + extraction + link following in `test_navigation_extract_follow_and_trace_preservation`; form flow in `test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file` |
| file download/artifact creation | `test_download_enters_canonical_file_and_artifact_path_with_provenance` verifies `file_*`, `artifact_*`, File↔Artifact linking, result/artifact/evidence refs and provenance |
| file upload | `test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file` verifies missing `file.read` is rejected and an authorized canonical file is uploaded |
| unsupported operation | `test_provider_replacement_unsupported_operation_and_disabled_path` verifies `browser.screenshot` is unsupported while a browser provider remains registered |
| timeout/cancellation | `test_timeout_and_cancellation_use_canonical_errors` |
| blocked domain/network policy | `test_network_policy_blocks_private_target` plus policy unit coverage |
| unauthorized side effect | `test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file` verifies no POST occurs before denial |
| session isolation | `test_session_isolation_by_project` |
| provider replacement/disabled path | `test_provider_replacement_unsupported_operation_and_disabled_path` |
| canonical error/trace preservation | browser capability tests plus `test_browser_failure_metadata_preserves_canonical_error_and_trace` |
| node/worker placement | `test_browser_binding_adds_worker_placement_without_changing_capability_contract` |
| URL/evidence redaction and observability | `test_browser_operation_metadata_is_redacted_and_enters_invocation_record` |

## Security invariants

- Raw cookies and credentials are not canonical payloads.
- Browser sessions are owner/project isolated by the reference adapter.
- Webpage text/HTML is labelled `untrusted_web_content`.
- Redirect destinations are policy-checked again.
- Default network policy blocks internal/private targets unless explicitly enabled.
- Browser operation telemetry strips URL credentials, query strings and fragments before storing
  ordinary invocation metadata.
- Provider errors retain canonical `ErrorCode` semantics; browser metadata is additive and
  namespaced.
- Upload bytes require the browser external-submit permission plus canonical `file.read`.
- Download persistence requires browser network-read, `file.create` and `artifact.create`.
- Downloaded bytes cross the canonical `FileProvider` seam and the resulting file is linked to a
  canonical `artifact_*` through the existing #13 file/artifact lifecycle rather than a
  browser-private artifact path.
- The reference adapter has no implicit access to a host browser profile.

## Deliberate non-requirements

The issue does not require one mandatory Chromium/Playwright/Selenium engine. Accordingly the
stdlib reference adapter proves the contract without JavaScript, screenshots or arbitrary DOM
click execution. Those are provider features for richer adapters, not missing canonical
platform dependencies. The implemented reference interaction surface (links, forms, file
upload/download and reusable sessions) is sufficient to exercise the canonical web-workflow,
security, file/artifact, placement and observability boundaries required by #74.

## Definition-of-Done assessment

At the code/contract level, #74 is complete when the PR's full repository CI passes on the current
`main` base. The remaining follow-up issues listed in #74 (#14, #16, #34, #37, #43, #17/#73) may
consume or harden this boundary later; they are not prerequisites for the browser capability
contract itself.
