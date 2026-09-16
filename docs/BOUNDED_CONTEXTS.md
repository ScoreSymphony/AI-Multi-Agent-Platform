# Bounded-context package map

Issue #895 refines the root-package ownership policy from [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md).
The package-boundary manifest remains the canonical ownership inventory; this document is the
higher-level navigation map. It groups the current root namespace into a small number of primary
contexts without pretending that every grouped package has the same lifecycle authority.

The goal is architectural legibility, not a big-bang rename. A contributor should choose a primary
context first, then the canonical owner inside that context.

## Architectural roles

The current root packages fall into five #895 roles:

| Role | Meaning |
| --- | --- |
| **Bounded context / durable owner** | Owns a durable platform responsibility whose contracts, state or security/lifecycle boundary justify independent ownership. |
| **Subdomain** | Canonical responsibility that belongs conceptually inside a broader primary context but may retain its current package while migration risk outweighs layout benefit. |
| **Infrastructure / adapter** | Replaceable integration, operator or runtime infrastructure; it must not become a second canonical domain authority. |
| **Application service / surface / read model** | User/operator composition or application-layer behavior over canonical owners. |
| **Compatibility / migration** | Historical import namespace whose canonical implementation belongs elsewhere. New behavior must not be added here. |

These roles complement, rather than replace, the `kind`, `owner` and `disposition` fields in
`PACKAGE_BOUNDARIES.toml`.

## Primary contexts

The target navigation model has twelve primary groups rather than treating all current root packages
as peer-level architecture concepts.

### 1. Foundation and control

**Durable owners:** `contracts`, `domain`, `kernel`, `control_plane`, `messaging`, `observability`.

These packages define shared platform contracts, canonical Task/Run lifecycle authority, northbound
control-plane ownership and cross-cutting event/telemetry contracts. Other contexts may depend on
these contracts, but this group must not become a generic dumping-ground named `core`.

### 2. Runtime and work coordination

**Durable owners / subdomains:** `agents`, `goals`, `planning`, `coordination`, `orchestration`,
`execution`, `task_management`, `handoffs`, `compensation`, `workflows`, `workspaces`, `verification`,
`evaluation`.

**Application/integration composition:** `coding_batches`.

This context owns how goals become planned, coordinated, executed and verified work. Package ownership
remains explicit: grouping these concepts does not transfer Task/Run authority away from the kernel or
provider authority away from `execution`.

### 3. Capability and model system

**Durable owners:** `capabilities`, `models`, `skills`, `browser`, `terminal`.

**Compatibility namespace:** `capability_assignments`.

Capability assignment policy is now canonically implemented at `capabilities.assignments`; the old
root package exists only for supported historical imports. Model-provider replaceability and governed
browser/terminal execution remain explicit boundaries even though they sit in the same primary
context.

### 4. Knowledge, context and repository intelligence

**Durable owners / subdomains:** `data`, `context`, `search`, `repositories`, `research`.

**Migration namespace:** `repository_intelligence` -> `repositories.intelligence`.

This group covers durable platform data, context assembly, search, source repositories and research
work. Repository intelligence is a repository subdomain rather than an independent root architecture
concept.

### 5. Security and governance

**Durable owners:** `security`, `governance`, `organizations`, `accounting`, `decisions`.

Security remains a hard boundary: grouping for navigation must never allow authentication,
authorization, approval or secret semantics to become implicit concerns owned by unrelated contexts.
Organizations and accounting may depend on security policy, not replace it.

### 6. Platform services

**Subdomains / application domains:** `conversations`, `automation`, `notifications`, `onboarding`,
`templates`, `portability`, `learning`.

These are first-class platform capabilities, but contributors should encounter them after the core
runtime/security/data architecture rather than as equally fundamental root concepts during initial
navigation.

### 7. Distributed runtime

**Durable owner:** `distributed`.

**Migration namespace:** `high_availability` -> `distributed`.

Node, Worker, scheduling, transport, leadership, fencing and failover belong to one distributed-runtime
context. The Control Plane HA contracts are already canonical at `distributed.control_plane_ha` and
`high_availability.contracts` is a compatibility re-export. Remaining HA service, integration,
telemetry and worker-transport code still has active runtime consumers, so its migration must remain
staged and evidence-driven rather than forcing a new directory shape.

### 8. Distribution, application delivery and managed applications

**Durable owners:** `distribution`, `application_distribution`, `applications`.

The names are related to application/component delivery, but their state authorities remain separate:

- `distribution` owns registry/catalog discovery, validation, install routing and generic Marketplace
  distribution semantics;
- `application_distribution` owns application build/distribution state, artifacts and release-gate
  integration;
- `applications` owns provider-neutral installed Application definitions, Application Instances,
  desired/observed runtime lifecycle, health, logical endpoints and recovery/reconciliation.

`applications` consumes existing placement, workspace/storage, security and catalog authorities rather
than redefining them. Concrete Docker/Podman/process/Kubernetes/remote-worker runtimes implement the
Application Runtime boundary without becoming canonical owners. ADR 0015 records this ownership
split. None of these three owners belongs to the distributed-compute context merely because an
Application may eventually run on a Node/Worker.

### 9. Integrations and extension infrastructure

**Infrastructure:** `adapters`, `connectors`, `plugins`.

These packages integrate external systems or optional providers through platform-owned contracts.
They must remain distinguishable from canonical domain code and cannot become lifecycle authorities.

### 10. Operations

**Operator infrastructure:** `configuration`, `deployment`, `backup`, `release`, `upgrade`.

These packages configure, deploy, protect, upgrade and release the platform. They consume canonical
state/contracts; they do not own replacement runtime semantics. `deployment` may compose an
Application Runtime backend at startup, but the managed Application lifecycle remains owned by
`applications`.

### 11. Client surfaces

**Application surface:** `cli`.

The CLI is a client/operator surface over the Control Plane and operator entrypoints. Frontend code is
outside the Python root-package map but follows the same authority direction: presentation does not own
shadow lifecycle state.

### 12. Quality and verification tooling

**Quality infrastructure:** `acceptance`, `benchmarking`, `conformance`, `testing`.

These packages validate the platform and provide reusable testing/benchmark support. They must not be
used as production domain owners.

## Dependency direction

The intended direction is deliberately simple:

```text
client surfaces / operations / integrations / quality
                    |
                    v
          canonical domain contexts
                    |
                    v
        foundation platform contracts
```

Additional rules:

1. A compatibility/migration namespace may depend on its canonical owner only to re-export supported
   behavior. The canonical owner must never import the compatibility namespace.
2. Adapters, connectors and plugins depend inward on platform-owned contracts. Canonical domains must
   not depend on a named optional adapter to define their semantics.
3. Operations may compose canonical domains but may not redefine Node/Worker/Task/Run/security or
   Application lifecycle ownership.
4. Security-sensitive boundaries remain explicit even when they share a primary navigation context.
5. Package moves must not change serialized resource/schema identity merely to make folders symmetric.
6. New root packages remain exceptional under `PACKAGE_BOUNDARIES.md`; normal growth happens inside an
   existing canonical owner or context.

## Measured dependency graph

`scripts/ci/package_dependency_audit.py` is the reproducible top-level dependency-graph audit. It
parses imports from the Python AST, reports every directed root-package edge and computes strongly
connected components with Tarjan's algorithm. The reviewed baseline lives in
`PACKAGE_DEPENDENCY_CYCLES.toml` and is exercised by
`tests/architecture/test_package_dependency_cycles.py`.

The first enforced #895 probe measured:

- **62** importable top-level packages;
- **434** directed top-level import edges;
- **1** cyclic strongly connected component;
- that SCC contained **47 packages**.

That result means the present architecture does not primarily have many isolated small cycles. Most
runtime/application packages participate in one large cyclic component. Treating that debt as dozens
of unrelated pairwise cycles would therefore be misleading.

The baseline is shrink-only: an SCC may split or lose members, but a current SCC must remain a subset
of an explicitly reviewed baseline set. A new SCC outside that set, or growth that pulls another root
package into the reviewed SCC, fails the architecture test. Before freezing the baseline, #895 moved
the last production Control Plane import of the `task_reassignment` compatibility namespace to
`task_management.reassignment`; the compatibility root is intentionally excluded from the allowed
SCC. This records an actual cycle-membership reduction rather than merely documenting existing debt.

The large SCC is a migration constraint, not a target architecture. Future focused work should shrink
it by moving internal callers off compatibility namespaces and by removing reverse dependencies at
real ownership boundaries. The guard intentionally permits shrinkage without requiring the whole SCC
to be eliminated in one risky refactor.

## Current migration state

| Historical root package | Canonical destination | State | Removal criterion |
| --- | --- | --- | --- |
| `task_reassignment` | `task_management.reassignment` | compatibility-only since #726; internal Control Plane caller migrated in #895 | Remove only after supported external callers have migrated and the normal public-import deprecation window permits removal. |
| `capability_assignments` | `capabilities.assignments` | compatibility-only in #895 | Same public-import deprecation rule; canonical code must not import the shim. |
| `repository_intelligence` | `repositories.intelligence` | planned, compatibility-sensitive | Move provider-neutral implementation only with production consumer migration and preserved plugin/import-string compatibility. |
| `high_availability` | `distributed` | partially migrated, higher risk | Contracts are already canonical at `distributed.control_plane_ha`; move remaining code only with HA restart/fencing/failover coverage and no deployment-ownership inversion. |

## Staged migration order

1. **Capability assignments** — low-risk ownership is already explicit; move implementation under
   `capabilities.assignments`, preserve root imports, and enforce object identity/direction.
2. **Compatibility-edge cleanup** — move internal callers from historical compatibility paths to the
   canonical owners so migration namespaces do not remain part of the canonical dependency SCC merely
   because first-party code still imports them.
3. **Repository intelligence** — migrate provider-neutral repository intelligence beneath
   `repositories` only when the production consumers and ProjectAtlas import-string compatibility are
   preserved explicitly.
4. **High availability** — continue the existing partial migration beneath `distributed` only after
   targeted restart/failover tests prove no lifecycle regression.
5. **Broader subdomain nesting** — consider additional physical moves only when dependency evidence
   shows a clear benefit. Documentation grouping alone is preferable to mass path churn for stable
   canonical owners.

## Boundaries that should remain explicit

The following separations are valuable even inside a smaller navigation model:

- `security` from ordinary product domains, because authorization/approval/secrets are trust
  boundaries;
- `execution` from concrete executors/adapters, because provider replaceability is a platform goal;
- `models` from concrete model providers for the same reason;
- `distributed` from `deployment`, because topology/scheduling semantics are canonical while deployment
  is operator composition;
- `distribution` from `distributed`, because component catalog/distribution is not distributed
  compute;
- `applications` from `application_distribution` and `deployment`, because an installed external
  application's desired/observed lifecycle remains canonical regardless of its build/release source or
  concrete runtime backend;
- `control_plane` from domain services, because northbound composition must not become domain
  ownership;
- adapters/integrations from canonical domains.

## Contributor rule

Start with this context map, then use `PACKAGE_BOUNDARIES.toml` to identify the canonical owner. If a
change appears to require a new root package, first prove that no existing primary context and owner
can contain it without mixing lifecycle, security, durable-state or replaceability responsibilities.