# CLI authorization and approval outcomes

Issue: #38
Security boundary: #15

The CLI remains a normal northbound client of the versioned Control Plane. It does not gain authorization because a command looks administrative and it does not bypass the canonical approval gate.

## Canonical outcomes

When #15 authorization is configured, the CLI surfaces the Control Plane's canonical error envelope unchanged apart from the existing output-redaction layer. Authorization metadata in `details` is produced by the server-side Control Plane and is not synthesized by the CLI.

A denied request is rendered with the canonical `forbidden` error and `details.authorization_outcome=deny`.

An approval-gated request is rendered with the canonical `forbidden` error and `details.authorization_outcome=require_approval`. The response may include the canonical `approval_id` and `requested_action_digest` supplied by the server so operators can correlate the blocked command with the approval workflow.

The CLI does not retry, mutate state, or downgrade either outcome into a client-side success.

## Exact-action behavior

Approvals belong to the canonical #15 authorization system and bind to an immutable proposed action. Once that exact action is approved, repeating the same CLI request observes the canonical allowed state through the same `/api/v1` route.

The CLI does not create its own approval cache or treat possession of an approval ID as permission.

## Current approval-management surface

The repository currently has the canonical #15 approval lifecycle and server-side authorization gate, but it does not yet publish a dedicated versioned Control Plane collection/command surface for listing, approving or rejecting Approval resources.

Accordingly, #38 must not invent `platform approval list|approve|deny` commands that call the ApprovalService directly. Those commands can be added only after the owning northbound API exists.

This does not block correct permission-error handling: CLI regression coverage uses the final composed `ControlPlaneHTTP` and the canonical #15 bridge to prove both `deny` and `require_approval` behavior end to end.
