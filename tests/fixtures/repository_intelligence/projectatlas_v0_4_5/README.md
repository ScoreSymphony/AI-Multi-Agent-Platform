# ProjectAtlas v0.4.5 golden-output capture fixture

This directory is intentionally a **capture contract**, not fabricated provider output.

The later unified integration campaign must populate exact raw JSON produced by the pinned ProjectAtlas v0.4.5 Linux x86-64 artifact after verifying the recorded archive SHA-256 and executing behind the accepted containment boundary.

Expected captured files:

- `scan.json`
- `search.json`
- `slice.json`
- `health-check.json`
- `settings.json`
- `capture-manifest.json`

`capture-manifest.json` must record at minimum:

- ProjectAtlas runtime version;
- release artifact SHA-256;
- upstream release/commit reference;
- platform/architecture;
- deterministic fixture revision;
- source-tree digest before/after execution;
- provider-state path and state size;
- containment evidence reference;
- command line for each captured payload;
- capture timestamp/environment reference.

Rules:

1. Do not hand-author or infer the JSON payloads from ProjectAtlas documentation.
2. Do not normalize before retaining the raw capture.
3. Review and explicitly identify volatile/provider-private identifiers before making a golden fixture stable.
4. The v0.4.5 normalizer must reject missing, renamed or wrongly typed required fields.
5. A different ProjectAtlas runtime version requires a separate fixture directory and explicit compatibility decision.
6. Raw captures are evaluation fixtures only; they are not canonical Repository/Workspace state.
