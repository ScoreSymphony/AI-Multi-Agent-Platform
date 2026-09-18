"""Maintained production security-boundary conformance inventory.

The matrix is evidence metadata only. Canonical security authority remains in the
owning authentication, authorization, approval, secret, workspace, execution and
verification subsystems; this module only aggregates their maintained acceptance
evidence for release conformance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecurityBoundaryClaim:
    surface_id: str
    surface: str
    authentication: str
    authorization: str
    approval: str
    scope: str
    secrets: str
    audit: str
    cancellation_failure: str
    fail_closed: str
    public_errors: str
    canonical_authority: str
    evidence: tuple[str, ...]


_SECURITY_BOUNDARY_CLAIMS = (
    SecurityBoundaryClaim(
        "control-plane",
        "Control Plane mutations",
        "Authenticated northbound request/session or scoped service credential.",
        "ControlPlaneAuthorizationBridge maps mutations to canonical authorization vocabulary.",
        "AuthorizationGate binds required approval to the exact proposed action digest.",
        "Server-derived owner/project/resource scope; caller scope cannot widen stored scope.",
        "Credentials are hashed/revocable and secret values are excluded from normal projections.",
        "Canonical authorization audit records retain actor/action/resource/correlation context.",
        "Authentication and authorization failures are contained before mutation.",
        "Missing policy composition fails closed outside explicit development opt-out.",
        "Northbound errors use canonical safe HTTP/contract projections.",
        "Client, credential and adapter metadata cannot become lifecycle or policy authority.",
        (
            "tests/integration/security/test_control_plane_authentication_hardening.py::"
            "test_credential_scope_denies_even_when_15_policy_allows",
            "tests/integration/security/test_authorization_final_boundaries.py::"
            "test_composed_control_plane_is_fail_closed_without_explicit_dev_opt_out",
            "tests/unit/security/test_authentication_service.py::test_expired_session_and_csrf_validation",
        ),
    ),
    SecurityBoundaryClaim(
        "capability-tool",
        "Tool/Capability execution",
        "Invocation actor/context is supplied by the authenticated canonical caller path.",
        "Capability policy executes before provider-side effects.",
        "Approval-required invocations bind to the canonical ToolInvocation payload.",
        "Capability/resource/workspace constraints remain canonical inputs to policy.",
        "Credential requirements are references/classification, not plaintext provider material.",
        "Invocation trace and governance evidence remain canonical and provider-neutral.",
        "Timeout, cancellation and provider errors normalize to canonical errors.",
        "Approval without a governance binding and denied policy both stop before execution.",
        "Provider diagnostics are translated instead of exposed as authority or raw failure state.",
        "Capability ID and ToolInvocation identity stay platform-owned across provider replacement.",
        (
            "tests/unit/capabilities/test_capabilities.py::test_permission_denied_before_provider_execution",
            "tests/unit/capabilities/test_capabilities.py::"
            "test_approval_required_is_bound_to_canonical_tool_invocation",
            "tests/unit/capabilities/test_capabilities.py::test_cancellation_is_mapped_to_canonical_error",
            "tests/unit/capabilities/test_capabilities.py::test_provider_error_is_mapped",
        ),
    ),
    SecurityBoundaryClaim(
        "mcp",
        "MCP calls",
        "MCP calls enter through the same canonical CapabilityInvocation actor context.",
        "MCP is an implementation behind Capability policy and egress enforcement.",
        "MCP side effects inherit canonical capability approval semantics.",
        "Canonical capability/project/data scope is evaluated before MCP transport execution.",
        "Secret data is blocked by the shared egress gate unless canonical policy permits it.",
        "MCP runtime identity is evidence metadata only; canonical invocation trace is retained.",
        "Transport/provider failures normalize through canonical capability errors.",
        "Unavailable or denying egress policy blocks before MCP provider execution.",
        "Raw MCP/provider identifiers do not become public canonical identities.",
        "MCP protocol/provider state never becomes authorization authority.",
        (
            "tests/unit/capabilities/test_capabilities.py::test_mcp_tool_uses_same_canonical_invocation_path",
            "tests/integration/security/test_capability_egress_paths.py::"
            "test_mcp_tool_is_blocked_by_same_egress_gate_for_secret_data",
        ),
    ),
    SecurityBoundaryClaim(
        "browser-network",
        "Browser/network actions",
        "Browser calls are reached through canonical authenticated capability/control-plane paths.",
        "Browser policy and canonical egress policy run before network or form side effects.",
        "Side-effecting form submission is approval/policy gated through the capability boundary.",
        "Browser sessions and File access are isolated by canonical project/workspace scope.",
        "Download/upload provenance and diagnostics redact sensitive material.",
        "Canonical traces retain browser action evidence without private session authority.",
        "Timeout and cancellation map to canonical errors and session state.",
        "Private/blocked targets and secret-data egress fail before provider execution.",
        "Browser diagnostics expose bounded canonical metadata, not cookies or backend handles.",
        "Browser/provider session identifiers never replace canonical project/File/Artifact identity.",
        (
            "tests/unit/browser/test_browser_capability.py::test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file",
            "tests/unit/browser/test_browser_capability.py::test_session_isolation_by_project",
            "tests/unit/browser/test_browser_capability.py::test_network_policy_blocks_private_target",
            "tests/integration/security/test_capability_egress_paths.py::"
            "test_browser_network_read_is_blocked_before_provider_execution_for_secret_data",
        ),
    ),
    SecurityBoundaryClaim(
        "terminal-process",
        "Terminal/process execution",
        "Terminal resources are exposed through authenticated Control Plane composition.",
        "Session access/input/termination is authorized server-side.",
        "Any approval requirement is enforced by the canonical Control Plane authorization path.",
        "Session access is actor/project/workspace scoped and read-only mode is enforced.",
        "Output/environment redaction prevents secret leakage from the terminal stream.",
        "Canonical session/frame audit identity is preserved independently of backend PTY handles.",
        "Termination and Run cancellation use canonical lifecycle paths with deterministic failure state.",
        "Unknown actors and unauthorized Run cancellation are rejected before backend mutation.",
        "Public terminal resources hide provider-private process/session handles.",
        "Terminal backend identity cannot confer access or cancel authority.",
        (
            "tests/integration/security/test_terminal_sessions.py::test_unregistered_actor_cannot_read_or_attach_session",
            "tests/integration/security/test_terminal_sessions.py::"
            "test_default_terminal_redaction_scrubs_sensitive_environment_assignments",
            "tests/e2e/security/test_terminal_run_cancel_authorization.py::"
            "test_terminal_termination_cannot_bypass_run_cancel_authorization",
        ),
    ),
    SecurityBoundaryClaim(
        "repository-git",
        "Repository/Git operations",
        "Repository operations are invoked from authenticated canonical service/capability paths.",
        "Repository writes run through canonical service policy before Git provider side effects.",
        "Approval semantics are inherited from the canonical repository/capability action.",
        "Repository materialization and mutations remain Workspace bounded.",
        "SecretReference and external repository identity do not leak into local clone paths.",
        "Canonical repository/revision provenance is retained for mutations.",
        "Provider unavailable/failure is translated without bypassing policy.",
        "Denied push/write stops before provider-side mutation.",
        "Public state exposes canonical repository identity rather than backend paths/handles.",
        "Replacing the Git provider preserves platform-owned repository identity.",
        (
            "tests/integration/repository/test_repository_capability_bridge.py::"
            "test_repository_operations_run_through_capability_registry_and_service_policy",
            "tests/repository_git_cases.py::test_push_is_denied_before_provider_side_effect",
            "tests/repository_git_cases.py::"
            "test_secret_reference_and_external_identity_do_not_leak_local_clone_path",
        ),
    ),
    SecurityBoundaryClaim(
        "connector",
        "Connector actions",
        "Connector actions originate from authenticated canonical callers/automation identities.",
        "Connector actions execute through the canonical capability/policy pipeline.",
        "Approval requirements remain capability/authorization owned, not adapter owned.",
        "Connection/project/resource scope is canonical and external IDs are namespaced metadata.",
        "Connector credentials are SecretReferences and missing/invalid credentials fail canonically.",
        "External references and action invocation IDs remain auditable.",
        "Connector health/provider failures normalize to canonical states/errors.",
        "Permission denial blocks the external action before adapter execution.",
        "External serialization exposes namespaced identity without raw credentials.",
        "External provider identity cannot grant platform authority.",
        (
            "tests/integration/connectors/test_connector_runtime.py::"
            "test_permission_denial_blocks_connector_action",
            "tests/integration/connectors/test_connector_runtime.py::"
            "test_resource_list_read_and_external_serialization_preserve_namespaced_identity",
            "tests/integration/connectors/test_connector_runtime.py::"
            "test_missing_and_invalid_credentials_fail_canonically",
        ),
    ),
    SecurityBoundaryClaim(
        "plugin",
        "Plugin actions",
        "Plugin lifecycle actions are reachable through canonical Control Plane composition.",
        "Plugin actions map to canonical plugin authorization resources.",
        "Permission-affecting enable/install actions require authoritative canonical resolution.",
        "Plugin/package identity is scoped by canonical installation/resource state.",
        "Plugin manifests cannot inject plaintext canonical secret authority.",
        "Lifecycle state and inspected manifest digest provide canonical audit evidence.",
        "Stale/uninspected manifest state is rejected before activation.",
        "Missing canonical permission resolution fails closed.",
        "Public plugin resources do not treat provider handles as policy grants.",
        "Plugin installation does not make plugin metadata an authorization authority.",
        (
            "tests/integration/plugins/test_plugin_control_plane.py::"
            "test_enable_requires_authoritative_permission_resolution",
            "tests/integration/plugins/test_plugin_control_plane.py::"
            "test_install_refuses_stale_or_uninspected_manifest_digest",
            "tests/integration/plugins/test_plugin_control_plane.py::"
            "test_plugin_actions_map_to_plugin_authorization_resource",
        ),
    ),
    SecurityBoundaryClaim(
        "application",
        "Application actions",
        "Application mutations enter through the authenticated canonical Control Plane.",
        "Authorization is evaluated before application mutation.",
        "Application configuration approval is payload bound.",
        "Application/project/runtime scope stays in canonical application records.",
        "Configuration uses canonical secret/reference boundaries rather than provider-owned authority.",
        "Configuration approval and mutation evidence is auditable.",
        "Runtime/provider failures are canonicalized at the Control Plane boundary.",
        "Authorization denial prevents mutation and provider failure cannot fabricate publication.",
        "Public errors are canonicalized rather than exposing backend-private state.",
        "Backend implementation choice cannot redefine canonical Application identity.",
        (
            "tests/unit/applications/test_application_control_plane.py::"
            "test_authorization_denial_happens_before_application_mutation",
            "tests/integration/security/test_application_authorization_approval.py::"
            "test_application_configuration_approval_is_payload_bound_and_audited",
            "tests/integration/application_distribution/test_control_plane_security.py::"
            "test_provider_failure_never_marks_canonical_release_published",
        ),
    ),
    SecurityBoundaryClaim(
        "worker-dispatch",
        "Local and remote Worker dispatch",
        "Worker registration/dispatch uses canonical authenticated service/worker identity.",
        "Dispatch authorization is canonical and evaluated before Worker execution.",
        "Approval policy remains platform-owned and cannot be widened by Worker metadata.",
        "Worker/Node/job identity and Workspace scope are validated across the transport boundary.",
        "Secret delivery is scoped, redacted and resolved only for the exact authorized dispatch.",
        "Canonical Run/job/correlation evidence survives remote execution and recovery.",
        "Lost/replayed/mismatched Worker messages fail deterministically without blind redispatch.",
        "Wrong identity, replay, unavailable secret policy and authorization denial all fail closed.",
        "Transport errors and Worker metadata are projected through bounded canonical errors/state.",
        "Worker-reported IDs/capabilities never become authorization authority.",
        (
            "tests/integration/security/test_worker_protocol_security.py::"
            "test_registration_rejects_wrong_worker_identity_and_incomplete_snapshot",
            "tests/integration/security/test_worker_protocol_security.py::"
            "test_worker_request_replay_is_rejected_by_authentication_boundary",
            "tests/integration/security/test_security_result_recovery.py::"
            "test_dispatch_authorization_denial_releases_reservation_before_worker_execution",
            "tests/integration/security/test_worker_secret_handling.py::"
            "test_authorized_secret_provider_denial_prevents_worker_dispatch",
        ),
    ),
    SecurityBoundaryClaim(
        "marketplace",
        "Marketplace install/update/uninstall",
        "Marketplace mutation commands are reached through the canonical Control Plane.",
        "Marketplace commands map to canonical authorization before owner mutation.",
        "Install/update approval binds to exact resolved permission/payload state.",
        "Installation and owner mutation use canonical Registry/Marketplace identity and scope.",
        "Artifacts/configuration are validated without turning embedded secrets into authority.",
        "Authorization/approval records and installation state retain mutation evidence.",
        "Provider/owner failures are translated and cannot fabricate successful installation state.",
        "Missing approval and policy denial stop before owner mutation.",
        "Marketplace errors expose bounded reason/kind/route metadata.",
        "Registry/provider identity remains source metadata; canonical installation owns lifecycle.",
        (
            "tests/integration/security/test_marketplace_authorization_enforcement.py::"
            "test_marketplace_install_requires_canonical_approval_before_owner_mutation",
            "tests/integration/security/test_marketplace_authorization_enforcement.py::"
            "test_marketplace_install_denial_stops_before_owner_mutation",
            "tests/integration/security/test_marketplace_authorization_enforcement.py::"
            "test_marketplace_approval_digest_binds_resolved_permission_state",
        ),
    ),
    SecurityBoundaryClaim(
        "automation",
        "Automation-triggered actions",
        "Automation uses a canonical automation/service identity and normal Task admission.",
        "Generated Tasks/actions are re-authorized at the canonical target boundary.",
        "Approval remains owned by the target action rather than webhook/schedule metadata.",
        "Requested project/revision scope is checked and stale target revisions cannot mutate newer state.",
        "Webhook verification uses references and never turns payload data into secret authority.",
        "Delivery identity/provenance is retained and duplicate delivery is auditable.",
        "Retry/overlap/failure behavior is deterministic and bounded.",
        "Spoofed webhook, revoked authorization and stale revision fail closed.",
        "Automation errors expose canonical status without raw credential material.",
        "External event/webhook identity cannot grant Task or action authority.",
        (
            "tests/unit/automation/test_automation.py::test_unauthorized_generated_task_fails_inside_normal_task_admission",
            "tests/unit/automation/test_automation.py::test_duplicate_webhook_delivery_does_not_duplicate_task",
            "tests/unit/automation/test_automation.py::test_spoofed_webhook_is_rejected_and_queryable",
            "tests/integration/goals/test_automation_runtime.py::"
            "test_stale_goal_revision_in_automation_delivery_cannot_mutate_new_revision",
        ),
    ),
    SecurityBoundaryClaim(
        "import-export",
        "Import/export",
        "Import/export is reached through canonical Control Plane/CLI actor context.",
        "Destination owner/provider validation occurs before mutation.",
        "Privileged imported state remains subject to destination authorization/approval owners.",
        "Canonical IDs/scopes are remapped explicitly and implicit owner transfer is rejected.",
        "Plaintext secret-bearing fields/runtime state are rejected; SecretReference placeholders are portable.",
        "Checksums, deterministic mapping and provenance provide import/export evidence.",
        "Failed imports compensate/roll back rather than leave silent partial authority.",
        "Unknown provider/owner, tampering and secret-bearing payloads fail before mutation.",
        "Validation failures use canonical portability errors without echoing sensitive fields.",
        "Imported provider/runtime identifiers cannot become canonical authority.",
        (
            "tests/integration/portability/test_portability.py::test_plaintext_secret_bearing_field_is_rejected",
            "tests/integration/portability/test_portability.py::test_secret_reference_placeholder_is_portable",
            "tests/integration/portability/test_portability_connectors.py::"
            "test_connection_import_rejects_implicit_owner_transfer_before_mutation",
            "tests/integration/portability/test_import_executor.py::"
            "test_executor_rolls_back_real_team_and_agent_in_reverse_order",
        ),
    ),
    SecurityBoundaryClaim(
        "secret-config",
        "Secret/configuration references",
        "Secret resolution occurs only inside authenticated/authorized owning runtime paths.",
        "Secret access is subordinate to canonical action authorization.",
        "Approval digest includes sensitive change intent without serializing plaintext secret material.",
        "Secret references carry canonical scope and cannot widen destination scope.",
        "Plaintext is excluded from canonical serialization, audit and normal public projections.",
        "Secret provider/audit records retain value-free access evidence.",
        "Unavailable/denying secret provider blocks before protected side effects.",
        "Unknown references and denied access fail closed.",
        "Redaction helpers cover operational API/export surfaces and direct SecretReference serialization.",
        "Secret-provider locator/backend metadata never becomes authorization authority.",
        (
            "tests/integration/security/test_authorization_exact_binding.py::"
            "test_secret_create_approval_binds_changed_material_without_plaintext_leak",
            "tests/unit/security/test_secret_provider.py::"
            "test_redaction_helpers_cover_representative_operational_api_and_export_surfaces",
            "tests/integration/security/test_worker_secret_handling.py::"
            "test_unknown_secret_reference_fails_before_worker_receives_job",
        ),
    ),
    SecurityBoundaryClaim(
        "approval",
        "Approval-gated operations",
        "Approver identity is authenticated independently from the requesting actor.",
        "Approval/rejection itself is authorized and requester scope cannot substitute stored project scope.",
        "Approval is immutable/exact-action bound and stale policy/action revisions invalidate reuse.",
        "Stored project/resource/task/run scope is authoritative for the decision.",
        "Approval records and audit contain references/digests, not plaintext secrets.",
        "AuthorizationGate records decision, policy, correlation and approval identifiers.",
        "Cancellation is shielded across committed security mutations and stale approvals are rejected.",
        "Missing, stale, wrong-digest or unauthorized approval never degrades to allow.",
        "Approval failures expose canonical outcome/policy/reference metadata only.",
        "Provider/private metadata cannot create or satisfy an Approval.",
        (
            "tests/unit/authorization/test_approval_project_scope.py::"
            "test_approval_decision_cannot_substitute_another_project_scope",
            "tests/integration/security/test_policy_lifecycle_revision_binding.py::"
            "test_enable_approval_is_invalid_after_policy_revision_changes",
            "tests/integration/cli/test_authentication_and_approvals.py::"
            "test_approval_contract_rejects_wrong_digest_unauthorized_actor_and_nonpending",
        ),
    ),
    SecurityBoundaryClaim(
        "verification-review",
        "Verification/review transitions",
        "Human/agent review enters through authenticated canonical reviewer identity.",
        "Verification reads/review commands fail closed on canonical Task/project scope denial.",
        "Review cannot substitute for authorization and exact subject revision is required.",
        "Verification request/policy/evidence stays bound to canonical Task/project/result/artifact scope.",
        "Review evidence and public projections do not expose credentials/provider-private state.",
        "Canonical Verification records preserve reviewer/outcome/evidence history.",
        "Changed revisions invalidate prior verification and bounded repair cannot rewrite history.",
        "Denied scope and ambiguous/invalid reviewer routing block completion/review transitions.",
        "Review errors and completion state use canonical verification outcomes.",
        "Verifier/model/provider identity cannot become completion authority outside Verification.",
        (
            "tests/integration/verification/test_verification_control_plane.py::"
            "test_verification_reads_and_human_review_fail_closed_on_task_scope_denial",
            "tests/integration/verification/test_verification.py::"
            "test_changed_result_revision_cannot_reuse_old_verification",
            "tests/integration/verification/test_verification.py::"
            "test_agent_reviewer_independence_and_read_only_rules_are_enforced",
        ),
    ),
)


def security_boundary_claims() -> tuple[SecurityBoundaryClaim, ...]:
    """Return the immutable maintained security-surface inventory."""

    return _SECURITY_BOUNDARY_CLAIMS


def security_boundary_pytest_nodes() -> tuple[str, ...]:
    """Return de-duplicated executable evidence nodes in stable matrix order."""

    seen: set[str] = set()
    nodes: list[str] = []
    for claim in _SECURITY_BOUNDARY_CLAIMS:
        for node in claim.evidence:
            if node in seen:
                continue
            seen.add(node)
            nodes.append(node)
    return tuple(nodes)
