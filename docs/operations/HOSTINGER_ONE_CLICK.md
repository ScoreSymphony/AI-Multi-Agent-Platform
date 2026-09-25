# Hostinger One Click / Docker Catalog packaging

## Status

The repository has a **catalog packaging candidate; it is not yet provider-validated or catalog-listed**.

The generic Hostinger Docker Manager **Compose from URL** path was tested on a real VPS and cannot
provide the required zero-input HTTPS/Open experience. The primary Hostinger product path therefore
targets Hostinger's native **One Click Deploy / Docker Catalog** provisioning surface.

Repository-side image publication and a candidate Compose input can be prepared independently.
The candidate must not be treated as Hostinger's final catalog format until #1476 obtains the
provider's actual onboarding/schema contract. Actual inclusion in Hostinger's public Docker Catalog
remains a provider-controlled step.

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

`.github/workflows/publish-container-images.yml` is the **single maintained publication workflow**
for the three Linux/amd64 GHCR runtime images:

- `ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane`
- `ghcr.io/scoresymphony/ai-multi-agent-platform-web`
- `ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway`

Every publication is bound to one exact source revision and includes the immutable SHA tag:

```text
sha-<full 40-character source commit SHA>
```

Main additionally publishes the moving `edge` convenience tag. Stable semantic-version Git tags
publish their version/major-minor metadata and may advance `latest` according to the release
metadata policy. Hostinger production catalog definitions must pin a stable semantic version or an
immutable digest rather than `edge` alone.

For provider validation before a release exists, run **Actions → Publish container images → Run
workflow** and enter the **exact 40-character source commit SHA**. The workflow checks out that
revision rather than the workflow-dispatch branch head.

The publication contract is intentionally strict:

- all three runtime images are prebuilt successfully before any publish job can start;
- the immutable SHA tag will not overwrite an existing tag;
- an exact semantic release-version tag will not overwrite an existing tag;
- source/ref mismatches fail before publication;
- OCI source, revision, component title and MIT-license metadata are attached;
- SBOM and provenance output remain enabled;
- each publish job records the source commit, immutable image ref and resulting registry digest in
  the workflow summary;
- pull-request events cannot publish packages;
- GitHub's scoped `GITHUB_TOKEN` is the only registry credential.

The moving `edge`, major/minor convenience tags and `latest` are never sufficient provenance for
Hostinger catalog acceptance; the immutable SHA ref/digest remains the candidate identity.

The three runtime packages must be **publicly pullable without credentials**. Package visibility is
an explicit publication prerequisite; Hostinger's catalog must never depend on credentials from the
user's GitHub account.

The publication workflow enforces this after every successful publish through the
`verify-public` matrix. Each verification job runs on a fresh runner with `packages: none`, an
empty isolated `DOCKER_CONFIG`, no registry login, and executes:

```bash
docker manifest inspect \
  ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane:sha-<40-character-source-commit>
```

The same exact-SHA anonymous check runs for Web and Hostinger Gateway. If any package is private or
otherwise not anonymously readable, the publication workflow fails instead of recording the source
revision as catalog-ready.

In addition, the non-publishing **GHCR public smoke** workflow checks the current `edge` manifest
for all three packages from pull requests that modify the publication/catalog contract. That gives
PR-time evidence of package-level public visibility before a new publisher change can reach
`main`.

## Catalog Compose input

`deploy/docker/docker-compose.hostinger-catalog-candidate.yml` is the repository-owned **candidate**
catalog packaging input. Its image/network/persistence boundaries are intentional, but the
provider-specific shape remains provisional until Hostinger confirms the catalog contract.

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

The catalog Compose currently accepts `AI_MAP_IMAGE_TAG` for packaging/testing and defaults to
`edge`, because `edge` is published immediately after the repository preparation reaches
`main`. A final public catalog listing must pin this to the provider-approved stable release tag
or immutable digest rather than expose image-tag selection to normal users.

## Provider onboarding packet

When Hostinger confirms its catalog submission path, provide at minimum:

1. project name: **AI Multi-Agent Platform**;
2. upstream repository:
   `https://github.com/ScoreSymphony/AI-Multi-Agent-Platform`;
3. license: MIT;
4. catalog Compose input:
   `deploy/docker/docker-compose.hostinger-catalog-candidate.yml`;
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
