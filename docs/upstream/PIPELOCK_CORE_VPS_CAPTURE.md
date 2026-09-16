# Pipelock Core ordinary-VPS evidence capture

This is the external measurement procedure for the Pipelock candidate evaluation. It does not replace the hosted Ubuntu reference; it produces the separate `ordinary-vps-reference` artifact required for the adoption decision. Historical context: issue #730 owns the original candidate evaluation and issue #810 retained the hosted reference benchmark.

## Boundary

Run the capture only on the representative ordinary Linux x86-64 VPS. The capture script deliberately
refuses GitHub Actions so hosted-runner measurements cannot be relabelled as VPS evidence.

The script:

- creates an isolated temporary Python virtual environment;
- installs the platform dev/MCP test environment and pinned test-only `websockets==15.0.1`;
- checks out exact Pipelock revision `f7d1816f1a5ad63d501b0c48f36066f836f59022`;
- builds the candidate through upstream `make build`;
- rejects an Enterprise-tagged binary using Go build metadata;
- runs the maintained Pipelock benchmark with the `ordinary-vps-reference` label;
- records the platform commit, Pipelock revision, binary/config/result SHA-256 values and host metadata;
- packages the retained evidence and a checksum for the final bundle.

No paid service is required. The temporary environment and upstream checkout are removed when the run
finishes; only the requested evidence directory remains.

## Command

From the `AI-Multi-Agent-Platform` checkout at the exact integration commit being evaluated:

```bash
bash scripts/benchmarks/run_pipelock_vps_capture.sh
```

An explicit output directory may be supplied as the only argument:

```bash
bash scripts/benchmarks/run_pipelock_vps_capture.sh /path/to/evidence/pipelock
```

Requirements are Linux x86-64, Python 3.12+, Git, Go, Make, `sha256sum`, `tar`, and outbound access to
clone the exact public Pipelock revision and install the platform's existing development dependencies.

## Retained files

A successful capture retains:

- `pipelock-performance-vps.json` — direct-vs-mediated latency, resource and FP/FN measurement;
- `pipelock-buildinfo.txt` — Go build metadata for the exact candidate binary;
- `pipelock-vps-evidence-manifest.json` — platform/Pipelock identities, hashes and host metadata;
- `SHA256SUMS` — checksums for the retained evidence files;
- `pipelock-vps-evidence.tar.gz` — portable evidence bundle;
- `pipelock-vps-evidence.tar.gz.sha256` — final bundle checksum.

The final recommendation must cite the retained VPS artifact rather than copying hosted-runner
numbers into the VPS evidence slot. The outcome remains exactly one of `adopt`,
`optional_provider`, `reference_only`, or `reject`.

Historical context: generated evidence manifests retain the original issue provenance where required for auditability.
