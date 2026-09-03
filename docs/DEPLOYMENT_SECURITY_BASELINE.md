# Stage-1 deployment security baseline

The single-node profile consumes the existing #34/#36/#43 boundaries rather than defining a
second security model:

- no credential is present in the example deployment configuration;
- password/session/API credential material is stored only as the verifier forms defined by
  #36;
- authentication does not grant administrator rights implicitly;
- the trusted local bootstrap installs a separate persisted #15 administrator policy;
- the default network bind is loopback;
- disabling secure cookies is rejected for non-loopback binds;
- backend SQLite/file/workspace/executor paths have no public listener.

TLS termination for externally reachable profiles belongs at an explicitly configured trusted
boundary and must not result in public debug/backend ports.
