# Pipelock Core performance and operability evidence (#730)

This document defines the repeatable performance/operability measurement used by issue #730 for the
pinned Pipelock Core candidate.

## Candidate boundary

- Upstream: `https://github.com/luckyPipewrench/pipelock`
- Reviewed revision: `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- Build: tag-free `make build` Core binary only.
- Audit configuration is generated with `pipelock init --preset audit --skip-canary` for MCP.
- The HTTP/WebSocket proxy benchmark uses the repository's audit fixture that explicitly allows the
  loopback test target.
- No Enterprise feature or hosted/paid Pipelock service is required.

#15 authorization and #591 data-egress policy remain canonical. These measurements evaluate only the
cost and behavior of the optional technical mediation layer.

## What the benchmark measures

`scripts/ci/issue730_pipelock_benchmark.py` records direct and Pipelock-mediated samples for:

- HTTP fetch latency;
- generic WebSocket echo round-trip latency;
- MCP Streamable HTTP tool-call latency through the platform MCP adapter;
- Pipelock `run` cold-start/listener-ready time;
- Linux `/proc` CPU time consumed by the persistent HTTP/WebSocket Pipelock process;
- maximum observed resident set size (RSS) for that persistent process;
- Pipelock process log size;
- files written under the isolated Pipelock `HOME`.

Each transport report contains count, mean, median, p95, minimum, maximum and direct-to-mediated
median/p95 deltas. Clean HTTP, WebSocket and MCP cases are also correctness gates. A clean case blocked
by Pipelock is counted as a false positive for this small clean corpus.

The benchmark deliberately does **not** relabel the maintained adversarial tests as performance cases.
False-negative evidence remains owned by the dedicated #730 security corpus. In particular, the
multi-request exfiltration test must remain visible as a security limitation rather than being hidden
inside a latency score.

## Resource-measurement boundary

The persistent process CPU/RSS measurement covers `pipelock run` while HTTP and WebSocket traffic is
exercised. MCP `pipelock mcp proxy` subprocess resource use is not folded into that RSS/CPU number;
MCP overhead is represented by direct-vs-mediated call latency. The JSON report records this scope so
it cannot be interpreted as whole-system memory accounting.

The Linux `/proc` resource fields may be `null` on unsupported platforms. The intended representative
operating environment for final #730 evidence is Linux x86-64.

## GitHub-hosted smoke versus VPS evidence

The pull-request workflow `Pipelock performance and operability` is a reproducibility and regression
smoke. GitHub-hosted runner measurements are **not** accepted as the final ordinary-VPS numbers because
runner load and hardware are not controlled. The emitted JSON records `github_actions: true` so those
artifacts cannot be confused with VPS evidence.

The same command is intended to be executed on the representative single-node VPS after building the
same pinned Core revision:

```bash
python scripts/ci/issue730_pipelock_benchmark.py \
  --pipelock-bin /path/to/pipelock \
  --mcp-config /path/to/pipelock-audit.yaml \
  --iterations 50 \
  --warmup 5 \
  --output artifacts/benchmarks/pipelock-730-vps.json
```

For final decision evidence, record alongside that JSON:

- VPS class / vCPU / RAM / kernel;
- platform commit;
- exact Pipelock binary SHA-256;
- exact generated configuration SHA-256;
- whether other user workloads were stopped during the run.

A VPS run is valid only when all clean correctness gates pass. The final adoption recommendation must
consider the measured overhead together with bypass/containment, outage/recovery, receipt integrity and
adversarial-corpus results; performance alone cannot justify adoption.

## Interpretation constraints

- A low latency delta does not prove complete mediation.
- A successful Pipelock proxy path does not prove that an uncontained process cannot open another
  network path; the #730 containment evidence covers that separately.
- `pipelock run` RSS/CPU does not include unrelated platform processes.
- GitHub-hosted measurements are smoke evidence only.
- Final false-positive and false-negative conclusions must use the maintained #730 corpus, including
  known multi-stage/cross-request limitations.
