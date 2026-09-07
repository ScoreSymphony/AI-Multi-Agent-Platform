# CLI authentication and credential handling

Issue #214 consumes the canonical #36 authentication/session/credential HTTP contracts. The CLI remains a northbound client and never reads the authentication store or other backend state directly.

## Commands

```text
platform auth login --username <name>
platform auth login --username <name> --password-stdin
platform auth me
platform auth status
platform auth logout

platform auth session list
platform auth session renew
platform auth session revoke <session_id>

platform auth credential list
platform auth credential create --purpose <purpose> [--expires-at ...] [--scope-json ...]
platform auth credential revoke <credential_id>

platform auth token activate --token-stdin
platform auth token clear
```

`auth token activate` is for an already-issued bearer/service credential. The token is validated against `/api/v1/auth/me` before it becomes active for the selected CLI profile.

## Canonical request path

All authenticated CLI commands retain the existing API-first architecture:

```text
CLI profile
   |
   +-- non-secret endpoint/owner metadata -> cli.json
   |
   +-- resolved authentication state -> dedicated credential store
                                      |
                                      v
                         authenticated HTTP transport
                                      |
                                      v
                              /api/v1 Control Plane
                                      |
                                      v
                          #36 authentication boundary
```

Browser sessions use the canonical `HttpOnly` session cookie returned by `/auth/login`; mutating session requests also send the canonical CSRF token. Renew rotates both values. Logout revokes the active browser session before clearing local state.

Bearer credentials use `Authorization: Bearer ...`. `AI_PLATFORM_TOKEN` may provide a process-local bearer credential and takes precedence over the credential store.

## Secret storage and output

Ordinary CLI profiles remain explicitly non-secret. Session cookies, CSRF tokens and bearer secrets are stored separately in `cli.credentials.json` beside the selected profile configuration by default. `AI_PLATFORM_CREDENTIAL_STORE` can select another path. The CLI writes the file atomically and requests mode `0600` on platforms that support POSIX permissions.

Passwords and tokens can be read from hidden input or stdin and are never copied into normal profile configuration. Login output excludes the session cookie and CSRF token. Personal credential creation consumes the #36 one-time secret into the credential store and removes that secret from rendered output. `auth status` reports only safe local metadata such as mode, expiry and credential ID.

The existing renderer continues to apply the platform redaction layer to all machine and human output.

## Failure semantics

Authentication failures remain canonical HTTP errors:

- missing/invalid/expired/revoked authentication -> `401` authentication error;
- authenticated actor lacking authorization -> `403` authorization error;
- transport failures remain transport failures rather than falling back to local authentication state.

Revoking a stored personal credential clears the local active bearer state when it refers to that credential. `auth logout` with an active bearer token only clears the local token; server-side revocation is explicit through `auth credential revoke` so logout cannot pretend to revoke a bearer credential it cannot canonically identify.
