# Hostinger Docker Catalog provider onboarding request

## Purpose

This document is the provider-contact packet for GitHub issues #1476 and #1473.

AI Multi-Agent Platform is an open-source, MIT-licensed, self-hosted multi-agent platform. The
repository is preparing a Hostinger-native **One Click Deploy / Docker Catalog** integration because
real Hostinger VPS testing established that generic **Compose from URL** cannot produce the required
zero-input HTTPS/Open experience.

Repository:

```text
https://github.com/ScoreSymphony/AI-Multi-Agent-Platform
```

## Why provider confirmation is required

Current public Hostinger documentation distinguishes:

- **Compose from URL** — generic Docker Compose deployment;
- **One Click Deploy** — Hostinger Docker Catalog deployment.

Current public documentation also shows that Docker Catalog applications receive generated temporary
Hostinger hostnames such as:

```text
<app>-xxxx.srv123.hstgr.cloud
```

and that public GHCR images can be consumed by Docker Catalog applications without credentials.

Relevant Hostinger documentation:

- https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/
- https://www.hostinger.com/support/hostinger-docker-catalog-applications/
- https://www.hostinger.com/support/how-to-change-the-domain-of-a-docker-project/
- https://www.hostinger.com/support/how-to-resolve-hostinger-vps-docker-app-catalog-deployment-errors/
- https://www.hostinger.com/contacts

As of 2026-09-26, the reviewed public documentation does not describe a self-service third-party
Docker Catalog submission API, public catalog manifest schema, or public repository for external app
definitions. The supported onboarding contract therefore needs direct Hostinger confirmation.

## Real Hostinger evidence already collected

The project has been tested on a real Hostinger Docker VPS.

Generic **Compose from URL** does not expose the VPS hostname to Compose interpolation:

```text
HOSTNAME=missing
TRAEFIK_HOST=missing
```

Runtime UTS hostname discovery works only after containers start, which is too late for hPanel's
precomputed **Open** target.

Publishing a gateway port makes hPanel show **Open**, but it points to:

```text
http://<VPS-IP>:<published-port>
```

rather than to an automatically generated HTTPS Hostinger hostname.

Therefore the remaining requirement depends on Hostinger's native Catalog/One Click provisioning
contract rather than an undocumented hPanel workaround.

## Repository package prepared for Hostinger

The repository already contains a provider-independent catalog packaging candidate:

```text
deploy/docker/docker-compose.hostinger-catalog-candidate.yml
```

It uses prebuilt GHCR images rather than Git build contexts.

Runtime images:

```text
ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane
ghcr.io/scoresymphony/ai-multi-agent-platform-web
ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway
```

Properties already enforced by repository tests:

- linux/amd64 runtime images;
- immutable source-SHA image identities;
- SBOM and provenance generation;
- Control Plane remains private;
- Web remains private behind the Hostinger gateway;
- secure cookies remain enabled;
- persistent named volumes are defined;
- no Docker socket;
- no Hostinger API key in the application stack;
- no arbitrary public high port in the catalog candidate;
- no fake hostname fallback;
- exact Host/HostSNI routing can be generated when provider metadata is known.

## Provider questions

Please confirm the supported process and requirements for adding this external open-source
application to Hostinger's VPS Docker Catalog.

### Submission and review

1. Does Hostinger currently accept third-party/open-source Docker Catalog submissions?
2. What is the supported submission/contact or partner route?
3. Can an application first be published privately/unlisted for provider acceptance testing?
4. What security, licensing, maintenance, or review requirements apply?

### Catalog definition

5. Is there a public or partner-only manifest/template/schema?
6. Does Hostinger consume a Docker Compose definition directly or transform it into provider-owned
   deployment metadata?
7. If a manifest exists, please provide its current schema/documentation and versioning rules.

### Images and registry

8. Are public GitHub Container Registry images supported for third-party Catalog applications?
9. Which architectures are required beyond linux/amd64, if any?
10. Must production entries pin a semantic version, immutable tag, or image digest?
11. Are there naming/namespace requirements for registry images?

### Automatic hostname and Open behavior

12. Which values are injected into a Catalog deployment, specifically:
    - `COMPOSE_PROJECT_NAME`;
    - `TRAEFIK_HOST`;
    - public URL/domain variables;
    - public port/service metadata?
13. How is the temporary `*.hstgr.cloud` hostname allocated?
14. How does hPanel decide which service receives **Open**?
15. Are Traefik labels supplied by the application definition or generated/augmented by Hostinger?
16. Is a shared Traefik Docker network required?

### Lifecycle and persistence

17. How should persistent volumes be declared so data survives Catalog upgrades/redeploys?
18. How are Catalog updates/version migrations delivered?
19. Are there required healthcheck/readiness conventions?
20. What happens to the temporary hostname and project identity during upgrade/reinstall?

### Catalog metadata

21. Which app metadata is required:
    - icon/logo;
    - screenshots;
    - description;
    - categories/tags;
    - upstream/project links;
    - license;
    - documentation/support links?

## Requested acceptance path

The preferred provider validation sequence is:

1. Hostinger supplies the supported third-party onboarding/schema contract.
2. The repository adapts the provisional catalog candidate only to that documented contract.
3. Hostinger exposes a private/unlisted test entry if supported.
4. A fresh deployment on the existing Hostinger test VPS is performed.
5. No manual VPS hostname is entered.
6. hPanel automatically shows **Open**.
7. **Open** resolves directly to a generated HTTPS `*.hstgr.cloud` hostname.
8. The application loads successfully.
9. Persistent state survives a provider-supported update/redeploy.
10. The resulting provider requirements are recorded in #1473/#1476.

## Contact route

Hostinger's public contact page directs existing customers to Hostinger support/live chat:

```text
https://www.hostinger.com/contacts
```

Until Hostinger provides a dedicated Catalog partner/submission route, this packet should be sent
through the authenticated customer support channel and explicitly requested to be routed to the
team responsible for **VPS Docker Manager / Docker Catalog / One Click Deploy**.

Do not reverse-engineer private hPanel endpoints as a substitute for provider confirmation.
