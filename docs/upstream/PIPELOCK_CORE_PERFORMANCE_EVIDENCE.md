# Pipelock Core performance and operability evidence

Issue #730 requires measured latency/resource/false-positive evidence before a final adoption decision.
This document defines the reproducible measurement boundary used for that evidence.

## Exact candidate

The benchmark workflow builds the reviewed Pipelock Core revision
`f7d1816f1a5ad63d501b0c48f36066f836f59022` through the tag-free `make build` path and rejects a
binary whose Go build metadata contains the Enterprise build tag.

## Harness

`scripts/benchmarks/issue730_pipelock_benchmark.py` runs a deterministic local comparison with the
same platform fixtures used by the #730 transport/security evaluation. It records JSON evidence for:

- direct HTTP versus Pipelock `/fetch` latency;
- direct WebSocket echo versus Pipelock `/ws` round-trip latency;
- direct MCP stdio tool-call latency versus `pipelock mcp proxy` latency;
- Pipelock `run` cold-start time;
- Pipelock process CPU time during the HTTP/WebSocket/classification workload;
- observed RSS start/peak/end values from Linux `/proc`;
- Pipelock HOME plus stdout-log startup size and workload growth;
- false-positive/false-negative counts for the maintained live HTTP DLP corpus subset plus explicit
  benign controls.

The MCP value is deliberately named `mcp_stdio_call_total`: the platform MCP SDK currently opens a
client/process context for each call, so the measurement includes process startup and protocol
handshake rather than pretending to be a persistent-session microbenchmark.

No arbitrary performance threshold is used as a CI gate. The workflow gates only on successful,
well-formed measurements and retains the observed numbers for the final #730 decision.

## Hosted-runner reference versus VPS evidence

The GitHub Actions workflow labels its result `github-hosted-ubuntu-reference` and the JSON contract
sets `hosted_runner_is_vps_evidence` to `false`. Hosted-runner measurements are useful for regression
and reproducibility, but they are **not** ordinary-VPS evidence and must not be presented as such.

### Retained hosted reference

Workflow run `34605287919` completed the full benchmark successfully against the exact reviewed pin.
The retained evidence artifact is `pipelock-performance-reference-evidence`, artifact ID
`10265199952`, with artifact SHA-256
`4f50735672b86fec5153506da4637d7fd790efe2bc2f3bd6268d79d0be26c00c`.

Observed GitHub-hosted Ubuntu reference values from that run:

| Measurement | Direct mean | Mediated mean | Mean delta | Relative overhead |
| --- | ---: | ---: | ---: | ---: |
| HTTP request | 18.311 ms | 19.486 ms | +1.176 ms | +6.42% |
| WebSocket round trip | 1.447 ms | 3.898 ms | +2.451 ms | +169.44% |
| MCP stdio call-total | 958.191 ms | 1112.430 ms | +154.239 ms | +16.10% |

The WebSocket percentage is large because the local direct baseline is about 1.45 ms; the absolute
observed increase in this run was about 2.45 ms. The MCP value includes process startup and protocol
handshake per call, as described above.

Additional hosted-runner observations:

- Pipelock `run` startup: `101.872 ms`;
- Pipelock CPU time during the HTTP/WebSocket/classification workload: `0.11 s`;
- observed RSS: `67,743,744` bytes at start and `68,575,232` bytes peak/end;
- Pipelock HOME plus stdout size after startup: `1,481` bytes;
- workload HOME/stdout growth: `25,844` bytes; end size: `27,325` bytes;
- maintained HTTP DLP subset: `2` true positives, `4` true negatives, `0` false positives and `0`
  false negatives.

These numbers are single-run reference evidence, not universal performance claims or statistical
accuracy guarantees.

The same harness is intentionally runnable unchanged on a Linux x86-64 VPS after building the exact
reviewed Core pin and installing the platform dev/MCP dependencies plus `websockets==15.0.1`:

```bash
python scripts/benchmarks/issue730_pipelock_benchmark.py \
  --pipelock-bin /path/to/pipelock \
  --config tests/fixtures/pipelock_websocket_audit.yaml \
  --output /tmp/pipelock-performance-vps.json \
  --environment-label ordinary-vps-reference \
  --iterations 50 \
  --warmups 5 \
  --mcp-iterations 10
```

A VPS result is acceptable evidence only when the retained artifact also identifies the exact
Pipelock binary/config hashes and the environment metadata in the JSON output. Until such a run is
retained, #730 must report representative VPS measurement as **pending**, rather than substituting the
hosted-runner reference.

## Classification scope

The FP/FN measurement reuses entries in `tests/fixtures/pipelock_adversarial_cases.json` that are both
`live` and `http_fetch_url` DLP/encoding cases, then adds deterministic benign controls. It records
case IDs and observed/expected classifications, but does not copy synthetic secret payloads into the
result JSON.

This is a maintained evaluation subset, not a claim of general statistical accuracy for arbitrary
real-world data. The broader #730 adversarial corpus remains the security-regression source of truth.

## Interpretation boundary

Performance evidence does not strengthen Pipelock's security boundary by itself. In particular, the
#730 containment work separately demonstrates whether traffic can bypass mediation. The final
recommendation must consider both the measured operational cost and the independently demonstrated
containment limitations.
