# Release metadata

This directory contains operator-facing, machine-readable release metadata for issue #42.

- `compatibility.json` is the reviewed compatibility snapshot for the current platform release and
  is mirrored into the Python package so installed deployments can query it without a repository
  checkout. Schema v2 binds the reviewed upstream pins to the complete canonical
  `VersionSnapshot`, including API/schema/protocol and deployment-specific adapter/plugin-interface
  version maps where those are claimed.
- `upstream-observations.example.json` documents the provider/CI-neutral input format for advisory
  update discovery.
- Runtime release manifests use release-manifest schema v2 shipped with
  `ai_multi_agent_platform.release`.
- Manifest v2 binds exact dependency lockfiles/resolved dependency sets, typed gate evidence,
  source commit, artifact digests, SBOM and provenance references into one release snapshot.
- Authoritative integration provenance remains in `upstream/*.yaml`; release artifacts copy the
  exact reviewed revision and attach release-specific hashes/SBOM/provenance rather than replacing
  those governance records.

Use `platform-release upstream-check --observations <path>` to compare an observation snapshot with
reviewed pins. `--disabled` and `--offline` make those states explicit without claiming that an
upstream is current. Candidate discovery never rewrites this directory or a running deployment.

A release candidate is accepted only through `platform-release validate --manifest <path>` after
its evidence has been populated by the release workflow. Commit-bound gates must reference the
exact release commit, and artifact/report/attestation evidence must carry a cryptographic digest.
Discovery is advisory; this directory is never an instruction to auto-update a running deployment.
