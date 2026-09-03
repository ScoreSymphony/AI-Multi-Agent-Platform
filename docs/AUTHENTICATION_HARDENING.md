# Authentication hardening addendum (#36)

This addendum documents the final issue-#36 controls layered on the provider-neutral
primitives described in `AUTHENTICATION.md`.

## Scoped authenticating credentials

The public self-hosted `LocalAuthenticationService` supports an optional
`CredentialScope` on personal, service, worker, automation and integration credentials.
Scopes use the canonical #15 `AuthorizationAction` and `ResourceType` vocabulary plus
optional exact resource IDs.

An empty scope is unrestricted **only with respect to the credential itself**. It does
not grant permission. A non-empty scope is a restrictive ceiling: the authenticated
request must satisfy the credential scope and then still pass the normal #15
authorization decision. Scope denial is therefore a `403` authorization outcome, not a
`401` authentication failure.

Credential secrets remain one-time output. Scope metadata is safe to enumerate and does
not contain verifier or secret material.

## Worker credential rotation and compromise handling

`rotate_worker_credential()` issues a replacement credential, preserves the prior scope
unless an explicit replacement scope is supplied, and revokes the previous credential in
the same lifecycle operation. The old secret stops authenticating deterministically.

`revoke_compromised_worker_credential()` is the explicit lost/compromised-worker path.
It revokes the referenced worker credential and emits a value-free authentication audit
record. No scheduler or heartbeat implementation is required for either operation.

## Authenticated request rate limiting

Login brute-force controls remain separate from authenticated request controls. The
public service accepts an `AuthenticationRequestRateLimiter` implementation. The
self-hosted default is `InMemoryRequestRateLimiter`, a deterministic sliding-window
reference implementation.

The Control Plane consumes the hook after canonical authentication and before protected
request dispatch. A rejected authenticated request returns `429 rate_limited`. The hook
is replaceable and does not depend on hosted IAM, telemetry or a specific deployment
topology.

## Security invariants

- Credential scopes can deny but never grant #15 permissions.
- Browser sessions are not converted into API-token scopes.
- `/auth/me` remains available for token introspection even when a token is scoped.
- Unknown or newly introduced protected operations fail closed when a restrictive scope
  cannot match their canonical action/resource target.
- Worker rotation never returns the old secret and audit metadata never contains secret
  values.
