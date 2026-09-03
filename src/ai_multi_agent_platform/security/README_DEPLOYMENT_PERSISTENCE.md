# Deployment persistence note

Issue #39 binds the completed #36/#15 local self-hosted security contracts to durable SQLite
implementations without changing their canonical semantics.

- `SqliteAuthenticationStore` subclasses the existing reference-store shape because #36
  currently mutates account/session/credential mappings directly. It persists only the
  password/token verifiers and safe metadata already defined by #36.
- `SqliteLocalAuthorizationProvider` persists `LocalPrincipalPolicy` records separately from
  authentication. A local user remains authenticated identity only until an explicit policy
  is installed.
- The deployment bootstrap coordinates these two stores in a retry-safe way rather than
  adding an authentication shortcut that implicitly grants administrator permissions.

A future storage-interface refactor may replace the dict-compatible store implementation,
but it must preserve these separation and secret-handling invariants.
