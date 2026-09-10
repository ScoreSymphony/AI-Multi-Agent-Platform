# Technical component catalog layout

The technical Marketplace remains a deterministic local/offline Registry backed by `FilesystemRegistryProvider`.

## Files

- `catalog.json` is the primary catalog and defines the canonical `provider_id`.
- `catalog.fragment.*.json` files are additive reviewed catalog fragments loaded from the same directory in filename order.
- `CURATION_QUEUE.md` is research/curation evidence only and is never loaded as Registry data.
- `artifacts/` contains reference-only catalog artifacts. Catalog entries do not vendor or install the referenced third-party projects.

Fragments use the same catalog schema as `catalog.json`. A fragment may omit `provider_id`; if present, it must match the primary catalog. Item/version identities must be unique across the primary catalog and every fragment. Schema mismatches, provider mismatches and duplicate identities fail closed.

Artifact paths are resolved relative to this catalog directory and may not escape it.

## Curation boundary

Splitting reviewed metadata across fragments does not change Registry trust or activation semantics. External technical candidates remain explicit `manual`/`untrusted` discovery or evaluation records unless a separate governed platform packaging/adoption path establishes otherwise.

A curation promotion must preserve verified source, license and project-status evidence. Unknown deployment, cost, network, provider, security or resource facts stay unknown rather than being inferred for presentation completeness.
