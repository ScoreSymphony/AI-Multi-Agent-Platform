# Private MCP retained-evidence validation

Issue #967 uses a versioned JSON report for the private remote MCP evaluation. Schema-valid JSON alone
is not sufficient evidence: every result-level evidence reference used by the live campaign must be
present in the top-level `raw_evidence` manifest, resolve inside an explicit evidence root and still
match its recorded SHA-256 digest.

The CLI added for that gate is:

```bash
platform-private-mcp-evaluation validate \
  --report tests/evidence/issue_967/<run-id>/report.json \
  --evidence-root .
```

A successful validation proves only report/file integrity. It does **not** convert synthetic evidence
into live two-VPS evidence and does not by itself satisfy #967.

To compute the decision state after integrity validation:

```bash
platform-private-mcp-evaluation assess \
  --report tests/evidence/issue_967/<run-id>/report.json \
  --evidence-root .
```

The command emits both the file-verification summary and the existing provider-neutral readiness
assessment. Optional gates are available for automation:

```bash
platform-private-mcp-evaluation assess \
  --report tests/evidence/issue_967/<run-id>/report.json \
  --evidence-root . \
  --require-decision-ready

platform-private-mcp-evaluation assess \
  --report tests/evidence/issue_967/<run-id>/report.json \
  --evidence-root . \
  --require-definition-of-done
```

Exit status `2` means the report or retained evidence is invalid. Exit status `3` means the report is
valid but an explicitly requested readiness gate is not satisfied.

## Path and hash rules

`raw_evidence[].path` is interpreted relative to `--evidence-root` and must:

- be a canonical forward-slash relative path;
- remain within the selected root after path and symlink resolution;
- identify a regular file;
- be unique in the manifest;
- match the recorded SHA-256 digest.

Every `evidence_refs[]` value attached to a result must also appear in `raw_evidence`. Ancillary
manifested files may remain unreferenced by an individual result when they provide campaign-level
context such as environment or topology capture.

For the repository-retained #967 campaign, use the repository root as `--evidence-root` so paths such
as `tests/evidence/issue_967/<run-id>/network/listeners.json` remain stable and auditable.
