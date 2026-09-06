# Release metadata

This directory contains operator-facing, machine-readable release metadata for issue #42.

- `compatibility.json` is the reviewed compatibility snapshot for the current platform release.
- Runtime release manifests use the JSON Schema shipped with
  `ai_multi_agent_platform.release`.
- Authoritative integration provenance remains in `upstream/*.yaml`; release artifacts copy the
  exact reviewed revision and attach release-specific hashes/SBOM/provenance rather than replacing
  those governance records.

A release candidate is accepted only through `platform-release validate --manifest <path>` after
its evidence has been populated by the release workflow. Discovery is advisory; this directory is
never an instruction to auto-update a running deployment.
