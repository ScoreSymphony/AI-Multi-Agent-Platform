# tzdata Runtime Adoption Review

- **Project name:** tzdata
- **Canonical upstream repository:** https://github.com/python/tzdata
- **Candidate version:** 2026.4
- **Integration category:** library dependency
- **Proposed platform boundary:** Python standard-library `zoneinfo` data-source fallback
- **Reviewer:** ScoreSymphony
- **Review date:** 2026-09-16
- **Related issues:** #1031, #1032

## Decision

**Approved** as a Windows-only required runtime dependency for the supported Windows baseline.

Python's standard-library `zoneinfo` module uses system IANA time-zone data when available and otherwise falls back to the first-party `tzdata` package. Supported Windows environments do not normally provide an IANA zoneinfo database in the standard search path, so the platform declares `tzdata==2026.4; platform_system == 'Windows'`. Non-Windows installations remain unchanged and continue to use their normal system time-zone data when available.

## Functional and architecture fit

- [x] Provides the IANA time-zone data required by the existing `zoneinfo`-based runtime behavior on Windows.
- [x] Uses Python's standard data-source fallback rather than introducing a platform-specific timezone abstraction.
- [x] No `tzdata` library type becomes part of a canonical platform API or persisted contract.
- [x] Lifecycle, persistence, authorization, distributed execution and deployment ownership are unchanged.
- [x] The PEP 508 environment marker keeps the dependency conditional to Windows.

## License and provenance

- [x] Canonical upstream verified as `python/tzdata`.
- [x] PyPI `tzdata` release `2026.4` was published on 2026-09-12.
- [x] PyPI provenance binds the release to tag `2026.4` and commit `b4d2086cb5a5ca5032ef8f6955057c49e5e12903`.
- [x] Package license verified as Apache-2.0 on 2026-09-16.
- [x] The bundled IANA time-zone source identifies the data as public domain.
- [x] No upstream source is copied, vendored, forked or selectively ported into this repository.
- [x] Installed distributions retain their own package/license metadata; no repository-level copied-source NOTICE is required for this integration mode.

## Compatibility and deployment

The platform baseline requires Python >=3.12. `tzdata 2026.4` is compatible with that baseline and is consumed through the standard-library `zoneinfo` fallback path. The dependency is required for the supported Windows baseline because Windows normally lacks the IANA database locations searched by `zoneinfo`; it is intentionally not made unconditional on systems that already provide system time-zone data.

The package is runtime data only. It requires no network endpoint, credentials, subprocess, database, GPU, hosted service or recurring paid service.

## Update and review method

Updates require an explicit dependency PR that verifies the target `python/tzdata` release and PyPI provenance, rechecks license/data treatment, preserves the Windows-only environment marker, and runs Windows timezone regression coverage plus release/dependency inventory validation and normal repository CI.

The release process must retain both views of Python dependencies:

1. the environment-resolved dependency set for the build host; and
2. the declared runtime dependency set from `pyproject.toml`, including PEP 508 environment markers.

This prevents a Linux release runner from silently omitting Windows-only requirements from release evidence.

## Exit and replacement

The dependency may be removed if the supported Windows baseline gains a reliable IANA time-zone database in the standard `zoneinfo` search path. A replacement must remain compatible with Python `zoneinfo` semantics and must not change canonical platform contracts merely to accommodate a data provider.

## Required follow-up before merge

- [x] Keep `tzdata==2026.4; platform_system == 'Windows'` pinned in `pyproject.toml`.
- [x] Add machine-readable provenance metadata.
- [x] Record the dependency in `docs/UPSTREAMS.md` and the direct-dependency table.
- [x] Bind declared Python requirements, including environment markers, into release evidence.
- [x] Add regression tests for the declared dependency inventory.
- [ ] Confirm full repository CI is green on the final PR head.

No ADR is required because the package supplies data to the existing standard-library timezone boundary and does not change canonical architecture.
