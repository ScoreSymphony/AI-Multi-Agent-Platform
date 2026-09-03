# CLI approval inspection

Issue #38 exposes the completed #15 approval lifecycle through a read-only canonical Control Plane collection before adding decision commands.

## Commands

Approval resources are discovered through the registered extension surface:

```text
platform extension list approvals --filter status=pending
platform extension show approvals <approval_id>
```

The collection exposes canonical approval identity and lifecycle metadata, including the exact action/resource binding, requester, risk/policy information, timestamps and `requested_action_digest`.

## Security boundary

The inspection surface never serializes the proposed action payload. The digest and an optional safe `payload_ref` are the only payload-related fields exposed by the approval contract.

The CLI remains a normal northbound client:

```text
CLI -> /api/v1/approvals -> ApprovalService read boundary
```

If the `approvals` collection is not registered, the CLI fails during OpenAPI extension discovery and does not access `ApprovalService` or another backend directly.

## Approval decisions

Approve/deny is intentionally not implemented as a direct `ApprovalService.decide()` call. #15 explicitly forbids that bypass: decisions must pass `AuthorizationGate.decide_approval()` so the approver and the stored exact action are authorized canonically.

The generic extension command boundary currently performs an additional coarse command authorization before dispatch. Until a gate-aware northbound approval-decision route is defined, the CLI must not invent a direct approval mutation path.
