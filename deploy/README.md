# Deployment surfaces

Repository-owned deployment assets live here. Runtime contracts remain defined by the platform
code and the canonical operations documentation under `docs/operations/`; this directory only
contains deployment-specific packaging and host integration.

## Layout

```text
deploy/
├── docker/         # Docker Compose, container images, edge config and container scripts
└── single-server/  # systemd/venv single-server deployment assets
```

### Docker

Use `deploy/docker/README.md` for the Docker Compose topology, Hostinger variants, recovery
override, persistent-volume behavior and ingress details.

The repository-root `docker-compose.yml` remains the normal production/self-hosted Compose entry
point, while `docker-compose.local.yml` is the explicit loopback development/CI profile.

### Single server

`deploy/single-server/` contains the maintained non-container single-server deployment surface.

## Placement rule

Keep host- or packaging-specific files under the relevant deployment surface. Shared platform
logic, lifecycle semantics, backup contracts and operator procedures belong in the platform source
or `docs/operations/`, not in provider-specific deployment folders.
