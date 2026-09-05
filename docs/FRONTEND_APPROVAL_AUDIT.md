# Approval web audit follow-up (#313)

This follow-up closes the two residual quality gaps found after the original #313 merge.

- `Shell` owns the single browser-session boundary and composes `ApprovalClient` from the same `session.fetch` used by the other authenticated clients. Approval pages no longer instantiate a second `BrowserSessionClient` or issue a second manifest request.
- Approval decision capability continues to derive from the already-loaded Control Plane manifest. Read-only Approval inspection remains independently available through the `approvals` resource when decision commands are absent.
- Render-level regression coverage proves that canonical Approval ID/action/resource/policy/digest context remains visible in degraded/read-only mode, unexpected secret-bearing proposed-payload fields are not rendered, explicit confirmation is required before decision buttons become actionable, and terminal Approvals remain read-only.

The canonical mutation boundary remains unchanged:

`Browser UI -> /api/v1 Control Plane -> AuthorizationGate.decide_approval() -> approval domain`

No browser path calls private Approval services or reconstructs hidden proposed payload values.
