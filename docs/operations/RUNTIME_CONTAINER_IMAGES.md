# Runtime container images

AI Multi-Agent Platform can publish repository-owned runtime images to GitHub Container Registry
(GHCR) for deployment surfaces that require prebuilt OCI artifacts, including Hostinger One Click /
Docker Catalog evaluation.

The source-build Docker Compose profiles remain supported. GHCR publication is an additional
distribution route, not a replacement for local/source builds.

## Image family

The maintained image names are:

```text
ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane
ghcr.io/scoresymphony/ai-multi-agent-platform-web
ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway
```

Each publication is built from one exact repository source revision. Every image receives an
immutable source-SHA tag:

```text
sha-<40-character-source-commit>
```

The workflow also adds OCI source, revision, title and MIT-license labels.

## Publication workflow

`.github/workflows/runtime-container-images.yml` is the only maintained publication workflow.

It supports:

- manual `workflow_dispatch` for an exact source commit/ref, primarily for Hostinger Catalog
  validation candidates;
- semantic-version tag pushes matching `v*.*.*`.

The workflow never runs on pull requests and uses only GitHub's scoped `GITHUB_TOKEN` with
`packages: write`. No long-lived registry password is required.

All three images are built before registry authentication/push begins. Before publication, the
workflow rejects an existing SHA tag and, for release events, rejects an existing release tag.
This prevents silently repointing an immutable source identity or an existing release version.

A semantic release tag is added only when the workflow is triggered by that Git tag. The release
tag must point at the same exact source commit as the checkout. The immutable SHA tag remains the
authoritative provenance reference.

No moving `latest` tag is published by this workflow.

## Manual Hostinger Catalog candidate

After this workflow is merged to the default branch, open **Actions → Runtime container images →
Run workflow** and set `source_ref` to the exact candidate commit SHA.

The resulting image references are shown in the workflow summary together with their pushed
digests. Hostinger Catalog packaging should consume the immutable SHA-tagged refs during provider
validation.

Do not use a pull-request head from an untrusted fork as a publication source.

## Architecture and cost

GHCR publication does not add a required recurring paid service. Public runtime images are intended
to remain compatible with the project's no-additional-paid-service deployment baseline.

The Hostinger Catalog/One Click integration is tracked separately in #1473. The existence of these
images does not imply that Hostinger has accepted the project into its Catalog.
