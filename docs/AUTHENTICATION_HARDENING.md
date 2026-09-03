# Authentication hardening addendum (#36)

This addendum documents the final issue-#36 controls layered on the provider-neutral
primitives described in `AUTHENTICATION.md`.

## Scoped authenticating credentials

The public self-hosted `LocalAuthenticationService` supports an optional
`CredentialScope` on personal, service, worker, automation and integration credentials.
Scopes use the canonical #15 `AuthorizationAction` and `ResourceType` vocabulary plus
optional exact resource IDs.

An empty scope is unrestricted **only with respect to the credential itself**. It does
not grant permission. A non-empty scope is a restrictive ceiling. Authentication only
establishes the actor and transports the credential scope as trusted context. The
canonical #15 authorization boundary evaluates that scope using the same action/resource
vocabulary as the normal policy decision. A request must pass the credential ceiling and
then still pass the configured #15 policy. Scope denial is therefore a `403`
authorization outcome, not a `401` authentication failure.

Credential scope is stored atomically with the credential record rather than in
process-local side state. Every stored credential contains an explicit complete scope
object (`actions`, `resource_types`, `resource_ids`), including unrestricted credentials.
Recreating the authentication service over the same store therefore preserves the exact
scope. Missing, malformed or unknown persisted scope metadata fails closed during token
authentication instead of silently becoming unrestricted.

Credential secrets remain one-time output. Scope metadata is safe to enumerate and does
not contain verifier or secret material.

## Trusted Control Plane propagation

The exposed HTTP/ASGI boundary never trusts caller-provided principal or owner headers as
authenticated identity. After authentication it attaches an internal, transport-neutral
`ActorContext` to the in-process `HTTPRequest`. That context contains the canonical actor
ID/type and a namespaced authentication trust context. External ASGI clients cannot set
this internal field.

The current composed `ControlPlane` carries the trusted actor metadata into the canonical
`AuthorizationRequest.trust_context`. `ControlPlaneAuthorizationBridge` then evaluates
the credential ceiling before delegating to the normal #15 gate. Passing the ceiling is
not an allow decision; the configured authorization provider still owns the final policy
outcome.

`AuthenticatedControlPlaneHTTP` delegates non-authentication routes to the current
composed Control Plane HTTP surface rather than inheriting a historical Search-era route
set. New Control Plane features therefore remain behind the same authentication boundary
without requiring a parallel authentication router update.

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

- Credential scopes are evaluated by #15 and can deny but never grant permissions.
- Scope state is part of the stored credential record and survives service recreation.
- Missing or malformed persisted scope state never expands permissions.
- Browser sessions are not converted into API-token scopes.
- `/auth/me` remains available for token introspection even when a token is scoped.
- Canonical action/resource mapping is owned by the authorization boundary rather than
  duplicated in the authentication router.
- Caller-supplied identity headers are never promoted into the internal trusted actor
  produced by authentication.
- Worker rotation never returns the old secret and audit metadata never contains secret
  values.
