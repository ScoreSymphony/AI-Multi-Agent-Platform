# Hermes Agent adoption review

Review date: 2026-09-03

## Candidate

- **Project:** Hermes Agent
- **Upstream:** `https://github.com/NousResearch/hermes-agent`
- **Pinned review revision:** `63279301bcbdc185c1b07b98a9312eb0c862f26d`
- **License:** MIT, verified from the upstream `LICENSE` file at the pinned revision.
- **Proposed integration:** optional external self-hosted service + platform-owned adapter.

## Functional fit

Hermes provides a production-oriented agent loop with programmable orchestration surfaces, including a documented HTTP API server. At the reviewed revision its programmatic-integration documentation exposes:

- `POST /v1/runs` for asynchronous agent-run admission;
- `GET /v1/runs/{id}` for pollable status;
- `GET /v1/runs/{id}/events` for lifecycle events;
- approval/steer/stop control endpoints;
- health and capability endpoints;
- alternative ACP and TUI-gateway protocols.

The HTTP run surface is sufficient for the first platform adapter without importing Hermes runtime classes.

## Architecture fit

Good, provided the dependency direction remains:

```text
platform canonical contracts -> platform Hermes adapter -> external Hermes service
```

Hermes is not allowed to own canonical Task/Run/Agent/Team/Plan/Step IDs or lifecycle state. The adapter translates Hermes-native run/session/model/tool references into namespaced adapter metadata only.

The #33 `AgentOrchestratorMapper` seam is used for exact Agent/Team revision mapping. The #5 `Orchestrator` contract is used for canonical planning.

## Replaceability

High. Hermes is disabled by default, no Hermes package is imported by core modules, and all persistent canonical state remains independent from Hermes. Replacement requires changing adapter/configuration selection rather than rewriting domain objects.

## License compatibility

MIT is compatible with the repository's MIT licensing for the proposed integration. No Hermes source is copied or redistributed, so the baseline does not require vendored copyright notices. Any future source copy/fork/port must repeat the license review and preserve the upstream notice.

## Activity and maintenance

The upstream repository is active at the review date. The pinned revision was committed on 2026-09-03. Because the project is fast-moving, the platform must pin a tested revision rather than implicitly follow upstream `main`.

## Security implications

Material but containable:

- the Hermes service can execute tools and may use credentials;
- API-server authentication is separate from platform authorization;
- Hermes can request approvals, but the baseline adapter does not auto-approve them;
- Hermes' tool catalog cannot replace canonical Capability allow/deny/approval policy;
- credentials are referenced through `api_key_env`, not serialized into canonical metadata;
- deployment should default to loopback/private networking unless explicitly hardened for remote use.

## Resource footprint

Hermes itself can run on a small VPS according to its upstream documentation, but actual resource needs depend heavily on model endpoints, enabled tools, browser/terminal backends and concurrency. The platform therefore records no fixed VPS/GPU topology as part of the adapter contract.

## Deployment complexity

Moderate. A separately managed Hermes service must be installed/configured and its API server enabled. This is preferable to embedding Hermes because it preserves independent upgrades, process isolation and replacement.

## Dependency footprint

The platform adapter adds no mandatory Python dependency: the baseline HTTP transport uses the standard library. Hermes retains its own dependency graph in its own deployment.

## API stability

The reviewed API is documented upstream, but Hermes is active and its API may evolve. Compatibility is therefore pinned to one commit and covered by adapter contract/integration tests. Dashboard/TUI implementation internals are deliberately excluded from the supported boundary.

## Migration / exit strategy

Disable/remove Hermes and select the reference or another `Orchestrator`/`AgentOrchestratorMapper`. Hermes-native run/session data may be discarded as adapter-private execution history once required diagnostics are captured; canonical platform history needs no migration.

## Simpler internal alternative

The existing reference/fake orchestrator already provides a deterministic baseline, but it intentionally lacks Hermes' production agent-loop/delegation capabilities. Reimplementing those capabilities in platform core would duplicate an active upstream project and weaken replaceability. An adapter is therefore the preferred reuse model.

## Decision

**Approved for optional adapter integration in issue #8**, subject to:

1. exact upstream revision recorded in provenance;
2. no Hermes source copied into the platform repository;
3. core/reference paths remain Hermes-free;
4. canonical IDs/lifecycle remain platform-owned;
5. explicit model/capability bridges fail closed;
6. real compatibility tests validate the pinned API before #8 closes;
7. upstream updates use dedicated review/PRs rather than silent revision changes.
