# Release metadata

This directory contains operator-facing, machine-readable release metadata for issue #42.

- `compatibility.json` is the reviewed compatibility snapshot for the current platform release and
  is mirrored into the Python package so installed deployments can query it without a repository
  checkout. Schema v2 binds the reviewed upstream pins to the complete canonical
  `VersionSnapshot`, including API/schema/protocol and deployment-specific adapter/plugin-interface
  version maps where those are claimed.
- `upstream-observations.example.json` documents the provider/CI-neutral input format for advisory
  update discovery.
- `upstream-validation-evidence.example.json` documents the revision-bound validation evidence
  required before a reviewed upstream candidate can be recorded into a new compatibility snapshot.
- `release-generation-input.example.json` documents the reviewed inputs consumed by deterministic
  release-manifest generation. It is a template: placeholder evidence must be replaced before a
  release candidate can pass validation.
- Runtime release manifests use release-manifest schema v2 shipped with
  `ai_multi_agent_platform.release`.
- Manifest v2 binds exact dependency lockfiles/resolved dependency sets, typed gate evidence,
  source commit, artifact digests, SBOM and provenance references into one release snapshot.
- Authoritative integration provenance remains in `upstream/*.yaml`; release artifacts copy the
  exact reviewed revision and attach release-specific hashes/SBOM/provenance rather than replacing
  those governance records.

Use `platform-release generate --source-commit <sha> --input <path> --output <path>` to assemble a
manifest from the exact source commit, canonical `VersionSnapshot`, reviewed compatibility inventory,
dependency/artifact files and explicit typed gate evidence. Generation hashes the supplied files and
validates the resulting manifest fail-closed; it does not invent passed gates, approvals, SBOMs or
provenance.

Use `platform-release upstream-check --observations <path>` to compare an observation snapshot with
reviewed pins. `--disabled` and `--offline` make those states explicit without claiming that an
upstream is current. Supplying `--data-dir <AI_MAP_DATA_DIR> --reviewed-at <RFC3339>` explicitly
persists the evaluated advisory report under the deployment data root. The operator service reloads
the latest persisted report after restart; malformed advisory state becomes an operator warning and
does not change production pins or #41 upgrade/version state.

A candidate that is ready for review still cannot be recorded from bare `passed` strings alone.
Create a validation-evidence document bound to the exact candidate revision and run
`platform-release upstream-adoption-check --observations <path> --component <name> --evidence <path>
--compatibility-status <state> --reviewed-at <RFC3339>`. The check requires passed
`adapter_contract_tests`, `eval_regression`, `security` and `compatibility_review` status plus typed
revision-bound evidence for every gate. Report/artifact/attestation evidence requires a SHA-256 or
SHA-512 digest. The command is read-only: it prints the resulting compatibility snapshot but never
rewrites production pins, commits changes or deploys an update.

The repository CI also treats `upstream/*.yaml` as the governance authority for important upstream
pins. Tests fail if the reviewed compatibility snapshot, packaged snapshot, Hermes runtime pin,
Hermes/Forge CI checkout revisions, LiteLLM optional dependency pin or the pinned LiteLLM integration
test drift away from the governed revisions.

`platform-release upstream-discover-git` is an optional provider-neutral Git-remote discovery
adapter. It uses immutable remote HEADs only as advisory observations. A changed revision is
classified `unknown` and therefore still requires manual review and the normal validation gates.
The scheduled workflow uploads observations/reports as CI artifacts; it never commits pin changes,
approves an update or deploys production.

A release candidate is accepted only through `platform-release validate --manifest <path>` after
its evidence has been populated by the release workflow. Commit-bound gates must reference the
exact release commit, and artifact/report/attestation evidence must carry a cryptographic digest.
Discovery is advisory; this directory is never an instruction to auto-update a running deployment.
