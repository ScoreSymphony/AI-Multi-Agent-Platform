# Hostinger One Click / Docker Catalog packaging

## Status

The repository is **catalog-ready, not catalog-listed**.

The generic Hostinger Docker Manager **Compose from URL** path was tested on a real VPS and cannot
provide the required zero-input HTTPS/Open experience. The primary Hostinger product path therefore
targets Hostinger's native **One Click Deploy / Docker Catalog** provisioning surface.

Repository-side packaging can be prepared independently. Actual inclusion in Hostinger's public
Docker Catalog remains a provider-controlled step.

## Provider evidence

The following Hostinger behavior has been verified from current public documentation and real
provider tests:

- Docker Manager exposes **Compose from URL**, **Compose manually**, and **One Click Deploy** as
  distinct deployment modes.
- **One Click Deploy** redirects to Hostinger's Docker Catalog rather than wrapping the generic
  raw-Compose API.
- Catalog applications receive default temporary Hostinger hostnames such as
  `<app>-xxxx.srv123.hstgr.cloud`.
- Generic raw-Compose projects expose mapped ports through `http://<VPS-IP>:<port>`.
- Real raw-Compose probes showed that neither `HOSTNAME` nor `TRAEFIK_HOST` is injected at
  Compose interpolation time.
- Publishing gateway `8080:8080` made hPanel show **Open**, but Open targeted the direct VPS
  IP/port rather than the generated HTTPS Hostinger hostname.
- Hostinger documents GitHub Container Registry credential failures as a Docker App Catalog
  deployment failure mode, confirming that catalog applications can consume registry images.

Relevant public provider documentation:

- https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/
- https://www.hostinger.com/support/how-to-change-the-domain-of-a-docker-project/
- https://www.hostinger.com/support/hostinger-docker-catalog-applications/
- https://www.hostinger.com/support/how-to-resolve-hostinger-vps-docker-app-catalog-deployment-errors/
- https://www.hostinger.com/support/connecting-multiple-docker-compose-projects-using-traefik-in-hostinger-docker-manager/

As of 2026-09-25, the reviewed public Hostinger documentation does **not** describe a self-service
third-party application submission API, public catalog-manifest schema, or repository into which an
external open-source project can submit a catalog definition. Do not invent or depend on private
hPanel endpoints. Catalog onboarding therefore requires direct provider confirmation/submission
instructions before #1473 can be considered complete.

## Published images

`.github/workflows/publish-container-images.yml` publishes three Linux/amd64 OCI images to GHCR:

- `ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane`
- `ghcr.io/scoresymphony/ai-multi-agent-platform-web`
- `ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway`

Main publishes:

- `sha-<full commit SHA>`;
- `edge`.

A stable version tag such as `v1.0.0` additionally publishes:

- `1.0.0`;
- `1.0`;
- `latest`.

Pre-release SemVer tags publish their version tags but do not advance `latest`.

Images are built with SBOM and provenance output enabled. Catalog production definitions should pin
a released semantic version or immutable digest rather than `edge`.

After the first publication, verify in GitHub Packages that all three packages are publicly
pullable without credentials. Hostinger's catalog should not depend on credentials from the user's
GitHub account.

## Catalog Compose input

`deploy/docker/docker-compose.hostinger-catalog.yml` is the repository-owned catalog packaging
input.

Unlike the generic Hostinger Compose profiles, it contains **no Git build contexts**. All services
pull prebuilt GHCR images. This avoids cloning/building the complete repository during One Click
deployment and makes the deployment artifact deterministic at a pinned image version.

The expected provider-owned catalog environment supplies:

- `COMPOSE_PROJECT_NAME`;
- `TRAEFIK_HOST`.

The catalog Compose intentionally has no top-level `name:` field. Hostinger must remain free to
assign its catalog project identity (including any uniqueness suffix) because that identity is part
of the generated temporary hostname contract.

The gateway renders exact project-scoped Host/HostSNI rules from those values. The application
stack publishes no arbitrary host port:

- Control Plane stays private on the platform bridge;
- Web stays private on the platform bridge;
- only the gateway is Traefik-addressable;
- TLS passes through to Caddy;
- Secure cookies stay enabled;
- no Docker socket, Hostinger API key, fake hostname, or host filesystem mount is added.

The catalog Compose currently accepts `AI_MAP_IMAGE_TAG` for packaging/testing. A final catalog
listing should pin this to the provider-approved release tag or digest rather than expose image-tag
selection to normal users.

## Provider onboarding packet

When Hostinger confirms its catalog submission path, provide at minimum:

1. project name: **AI Multi-Agent Platform**;
2. upstream repository:
   `https://github.com/ScoreSymphony/AI-Multi-Agent-Platform`;
3. license: MIT;
4. catalog Compose input:
   `deploy/docker/docker-compose.hostinger-catalog.yml`;
5. the three public GHCR image coordinates above;
6. internal application entrypoint: Hostinger gateway, ports 80/443;
7. persistent volumes:
   - `platform-data`;
   - `hostinger-gateway-data`;
   - `hostinger-gateway-config`;
8. no required user-supplied hostname;
9. no required Hostinger API key;
10. minimum provider metadata required for routing:
    `COMPOSE_PROJECT_NAME` and `TRAEFIK_HOST`;
11. required temporary-hostname/Open behavior:
    `https://<COMPOSE_PROJECT_NAME>.<TRAEFIK_HOST>`;
12. architecture currently packaged: linux/amd64.

If Hostinger requires a different catalog metadata format, generate that artifact only from the
documented schema/provider instructions. Do not infer a private schema from hPanel network traffic.

## Remaining #1473 gates

Repository preparation is not equivalent to catalog availability. #1473 remains open until:

- Hostinger confirms the supported third-party catalog onboarding mechanism;
- any required provider-specific metadata artifact is added from that documented contract;
- the three GHCR images are publicly pullable;
- the catalog entry is accepted/available to the test account;
- a fresh real One Click deployment creates all services without hostname input;
- hPanel shows **Open** automatically;
- Open resolves directly to a generated HTTPS `*.hstgr.cloud` hostname;
- persistent platform state survives redeploy/update;
- all repository CI/review gates and the exact post-merge `main` checks are green.

The explicit `docker-compose.hostinger-managed.yml` path remains an advanced/operator fallback and
must not be presented as equivalent to One Click acceptance.
