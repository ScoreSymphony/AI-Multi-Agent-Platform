# Issue #967 live evidence

This directory is reserved for measured two-node/two-VPS evidence for the private remote MCP transport
evaluation.

No synthetic or placeholder `report.json` is checked in as if it were measured evidence. A run may be
added only after its referenced artifacts exist and the report validates against
`private-mcp-transport-evaluation-report.v1.schema.json`.

Expected layout:

```text
<run-id>/
├── report.json
├── environment/
├── network/
├── authorization/
├── capabilities/
├── recovery/
├── lateral-movement/
├── latency/
└── hashes.sha256
```

All retained artifacts must be redacted. In particular, do **not** commit:

- zrok/OpenZiti enrollment or enable tokens;
- private identity/key material;
- MCP Gateway share/service tokens;
- platform session tokens;
- secret values resolved from `SecretReference` objects;
- raw provider-management credentials.

The report may retain non-secret component versions, source revisions, topology labels, canonical
correlation IDs where appropriate, redacted diagnostics, latency samples/statistics and SHA-256 hashes
of evidence files.

See `docs/evaluations/OPENZITI_PRIVATE_MCP_RUNBOOK.md` for the complete capture procedure.
