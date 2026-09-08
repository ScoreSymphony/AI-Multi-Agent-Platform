# ProjectAtlas v0.4.5 pilot evidence

> Verification snapshot: 2026-09-08

This record captures the first real third-party repository-intelligence pilot for issue #502. It is
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

The pilot deliberately does **not** use `projectatlas init`, install a ProjectAtlas plugin, or give
ProjectAtlas ownership of Repository/Workspace lifecycle.

The harness:

- creates a disposable Git fixture;
- records its immutable Git revision;
- makes canonical source read-only before ProjectAtlas execution;
- passes an explicit `--db` path in a separate provider-owned writable state directory;
- supplies a deliberately minimal environment without platform/CI secrets;
- runs bounded JSON commands only;
- restores permissions after execution and verifies the source-tree digest is unchanged;
- fails if `.projectatlas` state appears in the repository.

The tested commands are:

- `scan .`
- `search needle --limit 10`
- `slice src/demo.py --start-line 2 --end-line 2`
- `health-check --summary-only`
- `settings`

## Observed evidence

The successful integration run produced the following measurements on the tiny deterministic
fixture:

| Operation | Elapsed | stdout | User CPU | System CPU |
| --- | ---: | ---: | ---: | ---: |
| `scan .` | 37.78 ms | 890 B | 0.0203 s | 0.0122 s |
| `search needle --limit 10` | 30.49 ms | 839 B | 0.0240 s | 0.0050 s |
| exact `slice` | 30.27 ms | 193 B | 0.0241 s | 0.0050 s |
| `health-check --summary-only` | 32.46 ms | 112 B | 0.0255 s | 0.0059 s |
| `settings` | 37.84 ms | 8,043 B | 0.0337 s | 0.0041 s |

Additional observations:

- maximum reported child RSS: **19,032 KiB**;
- provider-owned state after the pilot: **820,744 bytes**;
- expected search hit returned: yes;
- exact source slice returned: yes;
- source read-only during provider execution: yes;
- source unchanged after execution: yes;
- project-local `.projectatlas` state created: no;
- provider database outside source: yes.

These figures are fixture evidence only. They are not a large-repository performance claim and must
not be generalized to production indexing workloads.

## Unresolved security gate

**Network isolation is not yet verified.**

The functional harness removes inherited secrets but does not create a network namespace or prove
that outbound connections are impossible. Issue #502 requires egress denial by default for an
untrusted repository-intelligence component, so this missing boundary blocks source/graph-capability
adoption even though the functional pilot passed.

## Current decision

ProjectAtlas remains **experimental / candidate**.

The platform may now carry a #20-compatible candidate plugin shell that:

- pins the expected runtime and upstream checksum in provenance;
- probes runtime identity without repository access;
- registers only `repository.health` and `repository.index_status`;
- requests capability-registration/worker-execution permissions only;
- requests no network, Workspace, or secret permission;
- reports source operations as disabled;
- can be enabled, disabled and removed through the canonical plugin lifecycle.

The shell must not expose repository map/search/slice/symbol/graph capabilities until all of the
following are demonstrated:

1. enforced network-egress denial for the provider process;
2. canonical Repository/Workspace authorization before provider access;
3. exact revision/dirty-Workspace freshness validation against #82/#37;
4. derived-state cleanup/rebuild semantics;
5. resource admission for initial/rebuild indexing;
6. provider-vs-baseline evaluation on representative coding/planning/review fixtures.

A later adoption decision must remain reversible: the deterministic Git/ripgrep/LSP baseline stays
fully usable when ProjectAtlas is disabled or absent.
