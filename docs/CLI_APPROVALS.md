# CLI approval inspection and decisions

Issue #38 introduced read-only #15 Approval inspection. Issue #214 completes the canonical decision workflow without giving the CLI access to Approval storage or lifecycle mutation primitives.

## Commands

The dedicated canonical surface is:

```text
platform approval list --filter status=pending
platform approval show <approval_id>
platform approval approve <approval_id> [--comment ...] [--idempotency-key ...]
platform approval deny <approval_id> [--comment ...] [--idempotency-key ...]
```

The existing generic read-only extension commands remain compatible:

```text
platform extension list approvals --filter status=pending
platform extension show approvals <approval_id>
```

Approval resources expose canonical identity and lifecycle metadata, including exact action/resource binding, requester, risk/policy information, timestamps and `requested_action_digest`.

## Decision security boundary

Before an approve/deny mutation, the CLI reads the canonical Approval and presents the safe action, resource, risk, policy and digest context to the user. Interactive use requires confirmation; non-interactive callers must pass `--yes`.

The mutation is sent only to the versioned Control Plane:

```text
CLI
 |
 | GET /api/v1/approvals/<id>
 | POST /api/v1/commands/approval.approve|approval.deny
 v
Control Plane
 |
 | compare caller requested_action_digest with stored Approval binding
 | require Idempotency-Key
 v
AuthorizationGate.decide_approval()
 |
 v
ApprovalService._decide_authorized()
```

The CLI never imports or calls `ApprovalService.decide()` or `_decide_authorized()`. The Control Plane does not expose a generic ApprovalService mutation primitive; it invokes the #15 `AuthorizationGate.decide_approval()` application boundary after exact-digest validation.

A changed digest is rejected before decision. An unauthorized approver is rejected by #15. Expired or otherwise non-pending Approvals conflict deterministically. A repeated identical Control Plane mutation with the same idempotency key returns the recorded safe result, while reusing that key for a different decision conflicts.

## Secret safety

The proposed action payload is never serialized by the Approval resource or decision response. The digest and an optional safe `payload_ref` are the only payload-related Approval fields. Confirmation text contains only safe canonical action/resource/risk/policy/digest metadata.

This keeps CLI and future Web clients on the same shared northbound contract; Web consumption remains owned by #313.
