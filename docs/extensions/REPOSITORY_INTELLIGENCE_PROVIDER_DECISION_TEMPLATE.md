# Repository-intelligence provider decision template

Use this record only after the unified #502 evaluation campaign. Do not mark a provider adopted from repository metadata, upstream claims, or the tiny ProjectAtlas pilot alone.

## Candidate identity

- Provider:
- Upstream repository:
- Exact version/tag:
- Exact commit/revision:
- License:
- Artifact/checksum/signature:
- Platform/runtime:
- Evaluation date:
- Evaluated by:

## Candidate role

Select the narrow role actually justified by measured capability:

- general repository-intelligence provider
- graph/impact specialist
- semantic/domain specialist
- experimental/reference-only
- rejected/deferred

List canonical capability IDs proposed for registration:

- 

List provider-specific features that cannot safely fall back to the baseline:

- 

## Security / containment evidence

- Repository/Workspace source read-only: pending
- Provider state outside source: pending
- Repository write/commit/push authority: denied
- Workspace/worktree lifecycle authority: denied
- Global platform secrets available: denied
- Network egress requirement: pending
- Network egress denied when not required: pending
- Privilege escalation prevented: pending
- Docker/socket authority: denied unless separately justified
- Exact process/container boundary:
- Security evidence reference:

Any unresolved item above blocks `adopted` status for source-processing capabilities.

## State classification

### Derived index

- State paths:
- Rebuild behavior:
- Cleanup behavior:
- Backup required: no by default
- Canonical truth: no

### Authored intelligence metadata

- Supported by candidate: pending
- If yes, exact authored fields:
- Persistence/export semantics:
- Backup/restore semantics:
- Promotion/review authority:

Do not enable authored provider metadata unless these semantics are explicit.

### Telemetry

- Collected metrics:
- Retention:
- Sensitive fields:
- Canonical truth: no

## Freshness / provenance

- Repository ID preserved:
- Requested revision preserved:
- Immutable resolved revision preserved:
- Workspace ID/snapshot/materialization preserved when applicable:
- Dirty-worktree freshness behavior:
- Stale detection behavior:
- Provider identity/version preserved:
- Source-slice provenance accuracy:

## Resource profile

For each workload record measured bounds and evidence rather than estimates.

### Bounded query

- CPU:
- RAM/peak RSS:
- Storage/state growth:
- Query latency:
- Concurrency:
- Host-pressure sensitivity:
- Evidence reference:

### Incremental refresh

- CPU:
- RAM/peak RSS:
- Storage/WAL growth:
- Refresh latency:
- Concurrency:
- Host-pressure sensitivity:
- Evidence reference:

### Full rebuild

- CPU:
- RAM/peak RSS:
- Storage/index growth:
- Rebuild time:
- Concurrency:
- Host-pressure sensitivity:
- Evidence reference:

Unknown heavy-work bounds must remain fail-closed for scheduler admission.

## Baseline comparison

Baseline provider:

`platform.repository-intelligence.baseline`

Comparison must use the same fixture, immutable source revision and environment reference.

| Metric | Baseline | Candidate | Delta / result |
| --- | ---: | ---: | --- |
| time to useful context | | | |
| Task success | | | |
| first-pass success | | | |
| agent/tool calls | | | |
| broad/full-file reads | | | |
| backtracking | | | |
| model context bytes/tokens | | | |
| provenance accuracy | | | |
| source-slice accuracy | | | |
| symbol correctness | | | |
| reference correctness | | | |
| dependency correctness | | | |
| architecture/domain/impact usefulness | | | |
| dirty Workspace freshness | | | |
| initial index | | | |
| incremental update | | | |
| full rebuild | | | |
| query/startup latency | | | |
| CPU/peak RSS | | | |
| provider state size | | | |
| repair/rebuild burden | | | |
| failure recovery | | | |
| network required | | | |
| secret required | | | |
| external service required | | | |
| paid service required | | | |

Unmeasured cells remain explicitly unmeasured; do not replace them with zero.

## Maintenance assessment

- Upstream maintenance activity:
- Upgrade cadence:
- Breaking-change risk:
- Runtime/package complexity:
- Language/runtime dependencies:
- Repair/rebuild burden:
- Local/offline operation:
- Additional recurring cost:
- Migration/removal path:

## Fallback and removal

- Baseline-equivalent capabilities fall back deterministically when provider is absent/stale/unavailable: pending
- Provider-specific Task requirements fail closed rather than silently degrade: pending
- Plugin can be disabled without core changes: pending
- Plugin can be removed without deleting canonical Repository/Workspace state: pending
- Provider derived state can be deleted/rebuilt independently: pending

## Decision

Choose exactly one:

- adopted
- specialist/on-demand
- experimental
- reference-only
- rejected
- deferred

### Rationale

Record measured benefits, security/resource/maintenance costs, and why the chosen role is the minimal non-redundant set relative to the baseline and other candidates.

### Conditions / follow-up

- 

### Registry status

A Registry/Marketplace listing remains an ordinary untrusted RegistryItem. Listing does not imply installation, activation, trust, repository write authority, secret access, network permission, or paid-service approval.
