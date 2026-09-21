# CLI authorization and approval outcomes

The CLI remains a normal northbound client of the versioned Control Plane. It does not gain authorization because a command looks administrative and it does not bypass the canonical authorization and Approval boundary.

## Canonical outcomes

When #15 authorization is configured, the CLI surfaces the Control Plane's canonical error envelope unchanged apart from the existing output-redaction layer. Authorization metadata in `details` is produced by the server-side Control Plane and is not synthesized by the CLI.

A denied request is rendered with the canonical `forbidden` error and `details.authorization_outcome=deny`.

An approval-gated request is rendered with the canonical `forbidden` error and `details.authorization_outcome=require_approval`. The response may include the canonical `approval_id` and `requested_action_digest` supplied by the server so operators can correlate the blocked command with the approval workflow.

The CLI does not retry, mutate state, or downgrade either outcome into a client-side success.

## Exact-action behavior

Approvals belong to the canonical authorization system and bind to an immutable proposed action. Once that exact action is approved, repeating the same CLI request observes the canonical allowed state through the same `/api/v1` route.

The CLI does not create its own approval cache or treat possession of an approval ID as permission.

## Approval-management surface

The Control Plane publishes the canonical `approvals` collection together with the exact-action `approval.approve` and `approval.deny` commands. The CLI exposes the same northbound surface:

```text
platform approval list
platform approval show <approval_id>
platform approval approve <approval_id>
platform approval deny <approval_id>
```

Approval decisions re-read the canonical Approval, send its exact `requested_action_digest`, and use the ordinary idempotency/correlation boundary. The CLI never calls `ApprovalService` directly and never treats possession of an Approval ID as authority. Server-side authorization, expiry, pending-state validation, decision identity and the canonical Approval lifecycle remain authoritative.

Permission-error handling and Approval management therefore use the same composed Control Plane surface as Web and other clients; no client-specific Approval lifecycle exists.
