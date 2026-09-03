# Authentication and Session Management

Issue: #36

## Purpose and boundary

Authentication answers **who is making a request**. Authorization from #15 answers
**what that authenticated actor may do**. The two decisions are intentionally separate.

The canonical path is:

```text
client credential / browser session
        -> authentication boundary
        -> AuthenticatedActor + ActorIdentity
        -> trusted RequestContext principal
        -> #15 authorization
        -> Control Plane operation
```

No authentication method grants administrative permissions by itself. In particular,
`bootstrap_first_admin()` creates the first local human identity but does not install or
bypass a #15 authorization policy.

## Canonical authenticated actor

`AuthenticatedActor` records:

- canonical platform `ActorIdentity` and `ActorType`;
- authentication method;
- session/credential reference, never its secret value;
- authentication timestamp and optional expiry;
- optional established organization/project context;
- request/correlation metadata;
- namespaced provider metadata for an external identity provider or worker transport.

External issuer/subject identifiers remain mappings to a canonical platform user. IdP
claims are retained as provider metadata only and do not become platform permissions.

## Local human accounts

The self-hosted baseline uses `ScryptPasswordHasher`, backed by `hashlib.scrypt`, with a
random per-password salt and a memory-hard verifier. Passwords are never stored in
plaintext or in reversible form.

Baseline defaults:

- scrypt `N=32768`, `r=8`, `p=1`, 32-byte derived key;
- minimum password length: 12 characters;
- enabled/disabled and locked/unlocked account state;
- password changes invalidate browser sessions by default;
- local operator password reset also invalidates browser sessions by default.

`InMemoryAuthenticationStore` is the deterministic reference store used by unit and
contract fixtures. It deliberately stores only password/token verifiers. Production
persistence can replace this storage boundary without changing `AuthenticatedActor`,
credential formats or Control Plane authentication semantics. The deployment issue must
bind authentication state to the deployment's durable persistence profile before claiming
restart-persistent account storage.

## First-user bootstrap and recovery

For an empty authentication store, `bootstrap_first_admin(username, password)` creates the
first local user. It is allowed exactly while no local users exist.

The name describes the operator bootstrap flow, not a permission grant. The deployment
bootstrap must separately install an explicit #15 policy for whichever administrator
rights are desired.

Password recovery is intentionally not exposed as an unauthenticated HTTP endpoint.
A trusted local/operator recovery workflow calls:

```python
service.reset_local_password(
    user_id,
    new_password,
    operator_ref="service:local-recovery",
)
```

The operation is auditable and invalidates existing browser sessions by default. A future
operator CLI may wrap this hook only under explicit local/recovery policy.

## Browser sessions

Browser login produces:

- an opaque random session secret in an `HttpOnly` cookie;
- a separate CSRF token;
- server-side session state containing only hashes/verifiers;
- creation/authentication/expiry/last-use/revocation timestamps.

The authenticated Control Plane uses `SameSite=Lax`; `Secure` is enabled by default and
may be disabled only for an explicit local development transport. State-changing requests
using a browser session require the CSRF token. Bearer-token clients do not use the browser
CSRF mechanism.

Supported lifecycle operations include:

- login;
- expiration;
- session rotation/renewal;
- logout;
- concurrent sessions;
- session enumeration;
- targeted revocation;
- global session invalidation after password change, account disable or account lock.

A revoked or expired session stops authenticating immediately.

## API, service and worker credentials

Opaque credentials contain a public credential identifier plus at least 256 bits of random
secret material. Only a SHA-256 verifier of that high-entropy random material is retained.
The raw credential is returned only at issuance time.

Credential records contain:

- credential ID;
- canonical owner identity;
- actor type and credential kind;
- purpose;
- creation/expiry/revocation timestamps;
- last-use timestamp;
- stored verifier, never retrievable secret material.

Kinds cover personal access, service, worker, automation and integration identities. The
kind must match its canonical `ActorType`; a token cannot change its actor class.

Authorization scopes are not embedded as implicit privileges in authentication tokens.
#15 evaluates permissions for the authenticated actor on every sensitive operation.

## Worker authentication contract

`create_worker_credential()` and `authenticate_worker_request()` provide the #36 side of
future #14 remote worker enrollment/registration.

The request-authentication fixture includes:

- a canonical worker identity;
- revocable/rotatable worker credential;
- timestamped request hook;
- nonce replay protection;
- optional TLS/mTLS peer reference metadata.

The TLS peer reference is evidence/transport metadata, not canonical worker identity. #14
will consume this authenticated identity for registration, heartbeat and dispatch without
making the scheduler a dependency of #36.

## Brute-force and rate-control hooks

`AuthenticationRateLimiter` is replaceable. The reference
`InMemoryFailureRateLimiter` applies a bounded failure window per normalized login key.
Unknown usernames still execute a dummy password verifier to reduce username-enumeration
timing differences.

A rate-control rejection is represented separately from invalid credentials and is mapped
to HTTP 429 at the northbound boundary.

## External identity-provider boundary

`IdentityProviderAdapter` verifies a provider assertion and returns only a
`VerifiedExternalIdentity` containing issuer, subject and namespaced metadata.

Authentication succeeds only after an explicit `ExternalIdentityMapping` links the exact
`(provider, issuer, subject)` tuple to a canonical local user ID. This prevents external
provider subject IDs or group/role claims from silently becoming canonical identity or
permissions.

OIDC/SSO implementations can be added behind this contract. Hosted identity is optional;
local authentication remains a valid self-hosted baseline.

## Authenticated Control Plane

`AuthenticatedControlPlaneHTTP` wraps the composed Control Plane HTTP boundary.

Public endpoints are limited to the platform root/health/readiness/OpenAPI plus local
bootstrap/login. Protected requests authenticate using either:

```text
Authorization: Bearer <personal/service/worker/... credential>
```

or the browser session cookie.

The boundary removes caller-provided `X-Principal-Ref`, `X-Owner-Type` and `X-Owner-Id`
values. It then injects only the canonical actor established by authentication before
passing the request to the existing Control Plane and #15 authorization boundary.

This prevents authentication from becoming a trusted-header convention at the exposed
client boundary while preserving the existing versioned Control Plane application
contracts internally.

### Local auth routes

```text
POST /api/v1/auth/bootstrap-admin
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout
POST /api/v1/auth/session:renew
GET  /api/v1/auth/sessions
POST /api/v1/auth/sessions/{session_id}:revoke
POST /api/v1/auth/password:change
GET  /api/v1/auth/credentials
POST /api/v1/auth/credentials
POST /api/v1/auth/credentials/{credential_id}:revoke
```

The personal-credential creation response contains the secret exactly once. List/detail
surfaces expose only safe metadata.

Service/worker/automation/integration credential issuance is available at the application
service boundary. Administrative APIs that expose those creation operations must first
apply #15 `manage_credentials` authorization; #36 deliberately does not create an
unauthenticated or role-name-based shortcut.

## HTTP error distinction

Authentication failures return `401 unauthorized` with `WWW-Authenticate` where relevant.
Rate limiting returns 429. Once authentication succeeds, a #15 policy denial remains
`403 forbidden`/authorization. This distinction is part of the public Control Plane error
contract.

## Audit and redaction

Authentication operations can emit `AuthenticationAuditRecord` through an injected sink.
Records contain canonical actor/subject/credential references and correlation metadata,
never raw passwords, session cookies, bearer tokens or assertions.

Audit metadata is passed through the platform's standard recursive secret redaction.
Normal session/credential serialization likewise excludes token and password verifiers.

## Security invariants

1. Authentication never grants authorization implicitly.
2. Raw passwords are never stored.
3. Raw session/API/service/worker secrets are never stored after issuance.
4. Secret comparison uses verifier-safe constant-time comparison where applicable.
5. Caller-supplied principal headers are untrusted at the exposed Control Plane boundary.
6. Revoked/expired sessions and credentials fail deterministically.
7. Browser state-changing operations require CSRF validation.
8. External identity claims never become canonical permissions automatically.
9. Worker request nonces can be rejected on replay before #14 exists.
10. Security-sensitive metadata is redactable and raw credential values are excluded from
    ordinary audit/log/resource representations.
