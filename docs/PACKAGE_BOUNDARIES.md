# Top-level Python package boundaries

Issue #726 defines the ownership policy for packages directly below `src/ai_multi_agent_platform/`.
The machine-readable inventory is [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml). Every
current top-level package appears there exactly once with a responsibility, owner, kind and current
disposition.

This is an incremental boundary policy, not a big-bang directory reorganization. Existing public
imports and persistence identities remain stable unless a focused migration explicitly changes them.

## Package kinds

The root namespace distinguishes the following package roles:

| Kind | Meaning | May own canonical domain semantics? |
| --- | --- | --- |
| `domain` | Durable platform responsibility with platform-owned contracts/state semantics. | Yes. |
| `surface` | User/operator client surface over canonical APIs. | No independent lifecycle authority. |
| `integration` | Adapter/plugin/connector boundary for replaceable integrations. | No; it implements or bridges platform-owned contracts. |
| `operations` | Deployment, release, backup, upgrade or configuration/operator composition. | No new canonical runtime authority. |
| `quality` | Testing, acceptance, conformance and benchmarking support. | No. |
| `migration` | Existing root package whose target owner is another package. | Only temporarily; new behavior belongs under the target owner. |

A package being retained at the root does not automatically make it a canonical domain. The `kind`
and `owner` fields are the source of truth for that distinction.

## Boundaries that are easy to confuse

### `deployment`, `distributed`, `distribution` and `high_availability`

These names are similar but the responsibilities are not interchangeable:

- `distributed` is the canonical owner for distributed execution topology: Node, Worker, WorkerJob,
  scheduling, worker transport, distributed runtime and distributed execution state.
- `deployment` is an operator/composition boundary: self-hosted profiles, process entrypoints,
  configuration wiring, startup/recovery composition and deployment-specific bindings. It may wire a
  distributed runtime, but it must not redefine Node/Worker/Scheduler semantics.
- `distribution` is the component registry/catalog domain: discovery, validation, registry items,
  installation and distribution of platform components. It is unrelated to distributed compute.
- `high_availability` currently owns Control Plane leadership, coordination leases, fencing and
  failover reconciliation. Those are distributed-runtime concerns, so its target owner is
  `distributed`. New HA behavior should be added under the distributed owner; the existing root
  package is migrated incrementally rather than removed in this issue.

`application_distribution` is also distinct: it owns application build/distribution state and
release-gate integration, not the general component registry and not distributed execution.

### `task_management` and `task_reassignment`

`task_management` owns canonical Task planning/application metadata such as priority, dependencies,
responsibility references and the Task management service.

Task-to-Project reassignment already depends on `TaskManagementService`, so reassignment belongs to
the `task_management` domain rather than forming another durable top-level domain. #726 applies that
low-risk consolidation directly:

- the canonical implementation now lives at `task_management.reassignment`;
- `ai_multi_agent_platform.task_reassignment` is a behavior-free compatibility namespace that
  re-exports the same public objects;
- architecture coverage asserts object identity across the canonical and compatibility imports;
- new reassignment behavior must be implemented under `task_management`;
- the compatibility namespace may be removed only under the repository's normal compatibility
  policy after supported callers have migrated.

The canonical implementation imports `TaskManagementService` from its sibling `service` module rather
than from the package root. This keeps the new ownership direction explicit and avoids introducing a
package-level circular import merely for re-export convenience.

### Other explicit consolidation candidates

The inventory records two additional narrow packages as migrations rather than new durable domains:

- `capability_assignments` -> `capabilities`;
- `repository_intelligence` -> `repositories`.

These are dispositions, not instructions to move all files in #726. Each move should be a focused,
behavior-preserving migration with supported-import compatibility where required.

## Rule for new functionality

New functionality extends an existing owner by default. A new module or subpackage inside an existing
domain is normal; a new package directly below `ai_multi_agent_platform` is exceptional.

A new top-level package is justified only when all of the following are true:

1. it represents a durable platform responsibility rather than one issue, feature, endpoint,
   provider, command or implementation detail;
2. no existing canonical owner can contain the responsibility without mixing materially different
   lifecycle/state ownership;
3. its public contracts, durable state ownership and dependency direction can be stated explicitly;
4. it remains meaningful when current concrete upstream providers/adapters are replaced;
5. the pull request updates `PACKAGE_BOUNDARIES.toml` with its kind, owner, disposition and a concrete
   responsibility rationale;
6. any material change to canonical ownership or public compatibility is documented in the relevant
   architecture documentation and, when required, an ADR.

"It keeps this feature in its own folder" is not sufficient justification for a root package.
Provider-specific, issue-numbered and one-feature-only packages should live below the canonical owner
or under the appropriate adapter/integration boundary.

## Migration namespace rules

A package marked `migration` is a compatibility or transition boundary, not a place for continued
independent growth.

- New canonical behavior goes to the package named by `owner`.
- Compatibility modules should be thin and bounded once the canonical destination exists.
- Supported import paths must either keep working or have an explicit migration/deprecation plan.
- Persistence identifiers and external API resource names are not renamed merely to match directory
  symmetry.
- A consolidation must not reverse canonical dependency direction to make the file move convenient.

## Coordination with #723

Issue #723 decomposes oversized modules by responsibility behind stable facades. That decomposition
must normally remain inside the canonical owner identified here. Splitting a large module into focused
nested modules/subpackages is encouraged; creating another root package as a by-product of the split
is not.

If #723 discovers that code is actually owned by another canonical domain, that is an ownership
change and should be handled as an explicit package-boundary decision rather than silently moved while
splitting a monolith.

## Review and CI guard

`tests/architecture/test_top_level_package_boundaries.py` compares the actual importable root package
set with `PACKAGE_BOUNDARIES.toml`. A newly created root package therefore fails the architecture test
until the same change records explicit ownership and responsibility.

The guard also validates package-kind/disposition values, owner references and migration ownership.
This is intentionally lightweight: it prevents accidental namespace growth without freezing the
architecture or forcing a deep hierarchy.

## Review checklist

For any change that adds or moves Python packages, reviewers should confirm:

- the existing canonical owner was considered first;
- new nested modules do not create a competing lifecycle/state authority;
- a new root package, if unavoidable, satisfies every criterion above and updates the inventory;
- migration packages receive no unrelated new behavior;
- supported imports and packaging discovery remain valid;
- changes that alter ownership rather than only layout are reflected in architecture documentation or
  an ADR as appropriate.
