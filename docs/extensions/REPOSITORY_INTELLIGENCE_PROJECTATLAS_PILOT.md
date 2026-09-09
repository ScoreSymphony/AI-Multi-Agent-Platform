# ProjectAtlas v0.4.5 pilot evidence

> Verification snapshot: 2026-09-09

This record captures the evaluated third-party repository-intelligence pilot for issue #502. It is
an evaluation record, **not** an adoption decision.

## Pinned upstream

- Upstream: `styler-ai/ProjectAtlas`
- Release: `v0.4.5`
- Release target commit: `72b424b7bb79b0d413dfb8c1bdd8eae9dc4e196b`
- License: MIT
- Pilot platform: Linux x86-64
- Pilot archive: `projectatlas-v0.4.5-x86_64-unknown-linux-gnu.tar.gz`
- Pilot archive SHA-256:
  `e22ac7f9e37b1eb49929e4a8ad72c51affd2668731832652ee4783e20a8fabd9`

The workflow verifies this checksum before executing the candidate and passes
`--require-version 0.4.5` on every repository-intelligence command.

## Containment shape

The pilot deliberately does **not** use `projectatlas init`, give ProjectAtlas ownership of
Repository/Workspace lifecycle, or expose the candidate plugin shell to repository source
capabilities.

The harness:

- creates a disposable deterministic Git fixture and records its immutable revision;
- makes canonical source read-only before ProjectAtlas execution;
- passes an explicit `--db` path in a separate provider-owned writable state directory;
- supplies a deliberately minimal environment without platform/CI secrets;
- sets `PR_SET_NO_NEW_PRIVS` before installing the Linux x86-64 seccomp filter;
- denies socket creation/network syscalls and rejects the x32 ABI in the evaluation process;
- proves the network boundary with a pre-provider `AF_INET` socket self-test that must return
  `EPERM`;
- closes unrelated inherited file descriptors;
- runs bounded JSON commands only;
- restores permissions after execution and verifies the source-tree digest is unchanged;
- fails if `.projectatlas` state appears in the repository.

The tested candidate commands are:

- `scan .`
- `search needle --limit 10`
- `slice src/demo.py --start-line 2 --end-line 2`
- `health-check --summary-only`
- `settings`

## Observed pilot evidence

The first successful contained integration run on the tiny deterministic fixture measured:

| Operation | Elapsed | stdout | User CPU | System CPU |
| --- | ---: | ---: | ---: | ---: |
| `scan .` | 37.78 ms | 890 B | 0.0203 s | 0.0122 s |
| `search needle --limit 10` | 30.49 ms | 839 B | 0.0240 s | 0.0050 s |
| exact `slice` | 30.27 ms | 193 B | 0.0241 s | 0.0050 s |
| `health-check --summary-only` | 32.46 ms | 112 B | 0.0255 s | 0.0059 s |
| `settings` | 37.84 ms | 8,043 B | 0.0337 s | 0.0041 s |

Additional observations from that run:

- process-lifetime child RSS high-water observed during the pilot: **19,032 KiB**;
- provider-owned state after the pilot: **820,744 bytes**;
- expected search hit returned: yes;
- exact source slice returned: yes;
- source read-only during provider execution: yes;
- source unchanged after execution: yes;
- project-local `.projectatlas` state created: no;
- provider database outside source: yes.

`RUSAGE_CHILDREN.ru_maxrss` is cumulative high-water evidence across terminated child processes; it
is not a per-command measurement. The final comparison artifact therefore records it only as a
whole-pilot observation and does not compare it to the Git baseline.

These figures are fixture evidence only. They are not a large-repository performance claim and must
not be generalized to production indexing workloads.

## Network evidence: evaluation boundary verified

The earlier statement that network isolation was unverified is obsolete for the **evaluation
pilot**. The current workflow runs every untrusted ProjectAtlas process through
`scripts/ci/issue502_no_network_exec.py`, which applies `no_new_privileges` and a seccomp filter that
denies socket/network syscalls. The harness fails unless an `AF_INET` socket creation attempt is
blocked with `EPERM` before ProjectAtlas executes.

This is intentionally narrower than a production claim. The candidate plugin shell still records
`network_isolation_verified=false` because enabling that plugin does not itself compose the
Linux seccomp evaluation wrapper around future source operations. The technical catalog's
`egress-unverified` wording should therefore be read as **production source-operation containment
not established**, not as denial that the evaluation pilot has real no-network evidence.

## Measured baseline comparison

The final #502 workflow now runs `scripts/ci/issue502_projectatlas_comparison.py`. It creates the same
deterministic immutable Git fixture for the candidate and the local Git reference baseline and
fails if the fixture revisions differ.

The machine-readable artifact compares like-for-like metrics for both paths:

- cold time to useful context;
- warm search/slice time to useful context;
- tool calls to useful context;
- persistent provider-state bytes;
- per-command elapsed/CPU measurements where available.

ProjectAtlas cold time includes `scan + search + slice`; warm time includes `search + slice`. The Git
reference path uses bounded `git grep` plus exact-revision `git show` extraction and has no persistent
index state.

Raw stdout byte counts remain available as transport observations but are **not** compared as model
context: ProjectAtlas and Git use different output envelopes. Normalized model-context bytes/tokens
remain explicitly unmeasured until both paths are reduced to the same logical payload. Comparable
baseline-vs-candidate peak RSS is likewise unmeasured.

The workflow deliberately does **not** convert unmeasured areas to zero. Representative agent
first-pass success, symbol/reference/dependency correctness, architecture/domain/impact usefulness,
large-repository rebuild cost and dirty-Workspace freshness for ProjectAtlas also remain outside
this tiny-fixture comparison.

## Final #502 decision

ProjectAtlas remains **experimental / deferred**.

The platform carries a #20-compatible candidate plugin shell that:

- pins the expected runtime and upstream checksum in provenance;
- probes runtime identity without repository access;
- registers only `repository.health` and `repository.index_status`;
- requests capability-registration/worker-execution permissions only;
- requests no network, Workspace, repository-write or secret permission;
- reports source operations as disabled;
- can be enabled, disabled and removed through the canonical plugin lifecycle.

The contained pilot plus real tiny-fixture baseline comparison are sufficient to keep ProjectAtlas
as an evaluated optional candidate. They are **not** sufficient to enable repository
map/search/slice/symbol/graph capabilities or make ProjectAtlas the default provider.

A future production source-capability issue would still need, at minimum:

1. exact v0.4.5 raw golden-output capture and strict negative-tested normalizers;
2. representative symbol/reference/dependency/impact correctness evidence;
3. dirty-Workspace and incremental freshness evidence for ProjectAtlas itself;
4. representative large-repository CPU/RAM/disk/update/rebuild measurements and admission bounds;
5. a deployment-owned production worker/process containment boundary rather than reusing the CI
   pilot as an implicit production sandbox.

None of those optional provider-adoption steps block completion of the clarified provider-neutral
#502 v1 core. The explicit scope decision is recorded in
`docs/adr/0011-repository-intelligence-v1-core-and-optional-providers.md`. The deterministic
Git/ripgrep/LSP-compatible baseline remains fully usable when ProjectAtlas is absent or disabled.
