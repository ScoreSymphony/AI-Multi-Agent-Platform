# Deployment portability invariant

Issue #39 treats local paths, ports, hostnames and process IDs as deployment configuration.
Canonical Task, Run, Project, Workspace, File and security identities remain platform-owned.

The current Stage-1 profile can therefore be recomposed with a different `AI_MAP_DATA_DIR`;
actual relocation of durable data is completed by #40 backup/restore rather than by turning
machine-local identifiers into canonical identity.
