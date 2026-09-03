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
        -> trusted RequestContext principal + authentication trust context
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
- namespaced provider metadata for an external identity provider, worker transport or
  credential-local authorization ceiling.

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
contract fixtures. It deliberately stores only password/token verifiers and safe
credential metadata. Production persistence can replace this storage boundary without
changing `AuthenticatedActor`, credential formats or Control Plane authentication
semantics. The deployment issue must bind authentication state to the deployment's durable
persistence profile before claiming restart-persistent account storage.

Credential scope is part of the authoritative `StoredCredential` record and must be
persisted atomically with the credential. A durable implementation must never persist a
credential while dropping or defaulting away its scope because that could widen authority.

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

Renewal revokes the previous server-side session before issuing a replacement. A revoked
or expired session stops authenticating immediately.

## API, service, worker, automation and integration credentials

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
- authoritative credential scope;
- stored verifier, never retrievable secret material.

Kinds cover personal access, service, worker, automation and integration identities. The
kind must match its canonical `ActorType`; a token cannot change its actor class.

### Credential scopes and #15

`CredentialScope` is a credential-local **deny-only authorization ceiling** expressed in
canonical #15 vocabulary:

- `actions` contains `AuthorizationAction` values;
- `resource_types` contains `ResourceType` values;
- `resource_ids` optionally restricts the credential to exact canonical resource IDs.

Empty dimensions mean that the credential adds no restriction for that dimension. An
unrestricted credential therefore still stores an explicit scope object with all three
fields present and empty.

The authority flow is:

```text
StoredCredential.scope
        -> validated authentication metadata
        -> trusted RequestContext authentication context
        -> canonical AuthorizationRequest.trust_context
        -> ControlPlaneAuthorizationBridge / #15
        -> credential-scope deny-only check
        -> normal #15 policy / approval decision
```

Passing the credential-scope check **never grants** an operation. The normal #15 provider
must still allow it or return the relevant approval outcome. Conversely, a scope denial is
final even if the principal's normal #15 policy would otherwise allow the operation.
Authentication credentials therefore never grant implicit administrator rights.

Malformed or incomplete persisted scope data fails closed during bearer authentication.
A credential is not silently widened to an unrestricted credential when scope data is
missing or corrupt.

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

Worker credential rotation creates a new credential, preserves the previous scope unless
an explicit replacement scope is supplied, and revokes the old credential. A compromised
or lost worker credential can be revoked independently from the future scheduler.

## Brute-force and request rate-control hooks

`AuthenticationRateLimiter` is replaceable. The reference
`InMemoryFailureRateLimiter` applies a bounded failure window per normalized login key.
Unknown usernames still execute a dummy password verifier to reduce username-enumeration
timing differences.

`AuthenticationRequestRateLimiter` is a separate replaceable hook for already
authenticated northbound requests. The self-hosted composition provides a deterministic
sliding-window `InMemoryRequestRateLimiter`.

A rate-control rejection is represented separately from invalid credentials and is mapped
to HTTP 429 at the northbound boundary. Broader distributed/resource abuse controls remain
part of later security and deployment hardening.

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

`AuthenticatedControlPlaneHTTP` wraps the current composed Control Plane HTTP boundary.
It authenticates the request first and then delegates authorization and the operation to
the current Control Plane composition rather than inheriting authority from a historical
HTTP implementation.

Public endpoints are limited to the platform root/health/readiness/OpenAPI plus local
bootstrap/login. Protected requests authenticate using either:

```text
Authorization: Bearer <personal/service/worker/... credential>
```

or the browser session cookie.

The boundary removes caller-provided `X-Principal-Ref`, `X-Owner-Type`, `X-Owner-Id` and
other caller-supplied identity projections. It then attaches an internal trusted
`ActorContext` containing only the canonical actor established by authentication. For
token credentials, the validated credential scope is carried in the trusted authentication
context so #15 can apply it as a deny-only constraint.

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
surfaces expose only safe metadata including the persisted credential scope.

Service/worker/automation/integration credential issuance is available at the application
service boundary. Administrative APIs that expose those creation operations must first
apply #15 `manage_credentials` authorization; #36 deliberately does not create an
unauthenticated or role-name-based shortcut.

## HTTP error distinction

Authentication failures return `401 unauthorized` with `WWW-Authenticate` where relevant.
Rate limiting returns 429. Once authentication succeeds, a #15 policy or credential-scope
denial remains `403 forbidden`/authorization. This distinction is part of the public
Control Plane error contract.

## Audit and redaction

Authentication operations emit `AuthenticationAuditRecord` through an injected sink when
one is configured. The #36 public self-hosted composition provides hooks for:

- successful/failed local login, including disabled or locked accounts;
- browser-session creation and successful/failed session authentication, including CSRF,
  expiry and revocation failures;
- logout and session revocation lifecycle events;
- successful/failed bearer credential authentication, including expired/revoked or
  malformed-scope failures;
- successful/failed worker request authentication, including replay rejection;
- successful/failed external identity authentication and provider verification failure;
- password change/reset and account-state changes;
- credential creation/revocation and worker rotation/compromise handling;
- authenticated request-rate-limit rejection.

Records contain canonical actor/subject/credential references, stable failure codes,
provider IDs where safe and correlation metadata. They never contain raw passwords,
session cookies, bearer tokens, CSRF values or external IdP assertions.

Audit metadata is passed through the platform's standard recursive secret redaction.
Normal session/credential serialization likewise excludes token and password verifiers.
#16 may later enrich or export these audit hooks, but #36 does not depend on the
observability stack for the hooks themselves to exist.

## Security invariants

1. Authentication never grants authorization implicitly.
2. Raw passwords are never stored.
3. Raw session/API/service/worker/automation/integration secrets are never stored after
   issuance.
4. Secret comparison uses verifier-safe constant-time comparison where applicable.
5. Caller-supplied principal headers are untrusted at the exposed Control Plane boundary.
6. Revoked/expired sessions and credentials fail deterministically.
7. Browser state-changing operations require CSRF validation.
8. External identity claims never become canonical permissions automatically.
9. Worker request nonces can be rejected on replay before #14 exists.
10. Credential scope is stored atomically with the credential and is a deny-only #15
    constraint; malformed persisted scope fails closed.
11. Security-sensitive authentication decisions expose redacted audit hooks without
    requiring #16.
12. Raw credential values are excluded from ordinary audit/log/resource representations.
