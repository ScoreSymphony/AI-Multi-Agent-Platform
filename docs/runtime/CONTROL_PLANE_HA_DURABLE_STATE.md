# Control Plane HA durable-state capability map

This document is the architecture inventory for #956 and the durable-state prerequisite of #566. The machine-readable source of truth is `docs/runtime/control_plane_ha_state_capabilities.toml`; an architecture test keeps that map aligned with the current single-node backup inventory.

The purpose is not to make PostgreSQL canonical. It is to make the **initial optional active/passive profile explicit about every state source that can affect correctness after promotion**.

## Classification

Every state source is classified as exactly one of:

1. **shared SQL** — canonical state that the active and standby must access through the HA SQL composition;
2. **shared provider** — canonical file/workspace/object content that must be reachable from the promoted process through an explicitly shared or replicated provider;
3. **reconstructable** — volatile state that may be rebuilt safely from shared canonical state or live peers;
4. **unsupported** — state that remains host-local for the initial profile and therefore requires an explicit fail-closed capability gate.

`initial_support = required` means #566 must not claim its baseline failover scenario until that state has the required shared implementation. `conditional` means the initial profile may exist without the capability, but enabling the capability while its state is host-local must fail closed. `single_node_only` marks existing #39/#41 state that is deliberately not an HA authority.

## Baseline inventory

The current `SINGLE_NODE_DURABLE_STORES` contract contains **37** database/JSON stores. The HA audit adds one currently unregistered persisted store discovered in `build_single_node_deployment` (`db/model-routing-profiles.json`) plus provider/volatile state that is outside the backup store list.

The resulting capability map contains **44 state sources**:

- 37 entries from the current single-node backup inventory;
- `model-routing-profiles`, which is persisted by the production-shaped single-node composition but is currently absent from `SINGLE_NODE_DURABLE_STORES`;
- shared file content;
- shared workspace content;
- managed repository working-tree content;
- volatile observability export buffers;
- live Worker heartbeat/registration state;
- shared HA schema/migration compatibility metadata.

The `model-routing-profiles` mismatch is intentionally recorded rather than silently normalised. It is a pre-existing backup/recovery inventory gap and must be resolved at the #40/#41/#891 boundary; #956 still classifies it because HA promotion cannot ignore execution-routing state merely because the backup inventory omitted it.

## Minimum shared state for the #566 baseline

The first supported active/passive profile requires shared persistence for:

- kernel Task/Run/Event history, revisions and idempotency;
- durable plan/step coordination;
- Control Plane scopes;
- File metadata;
- Workspace metadata and Run/Workspace bindings;
- authentication credentials, sessions and revocations;
- authorization policy, approvals and authorization audit continuity;
- Automation definitions, trigger/delivery/single-fire evidence;
- distributed Node/Worker reservations and dispatch/reconciliation records;
- shared HA schema/migration compatibility metadata.

File and Workspace **content** are separate from SQL metadata. A supported HA capability must either use a provider reachable after promotion or reject work that depends on host-local content. Sharing only metadata does not make host-local bytes HA-safe.

## Reconstructable state

Two state classes are explicitly safe to rebuild for the initial profile:

- volatile observability/export buffers, because they do not own canonical authority;
- live Worker heartbeat/registration state, because Workers re-register after promotion while durable reservations and dispatch/reconciliation records remain shared.

Reconstruction must not be used as a shortcut for canonical idempotency, authorization, approval, Automation or dispatch ownership state.

## Explicitly gated initial capabilities

The map deliberately does **not** require every current feature to be ported to PostgreSQL before the first #566 acceptance fixture. Repository management, Connectors, Memory/Knowledge, Research, Handoffs, Verification, Evaluation, Learning, Notifications, Agents, Conversations, model/onboarding JSON stores, Templates, Workflows and Capability Assignments are initially conditional.

That does not mean their local state is acceptable during HA operation. It means the HA composition must reject or disable the corresponding capability until a shared provider exists. A standby may not become write-ready while a capability advertised as supported depends on one of these host-local stores.

Repository working trees are a separate content concern: a host-local managed checkout is not shared canonical state. A future HA-capable repository path may either use shared content or prove deterministic rematerialization from shared provenance before the capability is advertised as supported.

## Upgrade and schema state

The single-node JSON files `platform-upgrade.json`, `migration-history.json` and `upgrade-history.json` remain #39/#41 recovery evidence. They are not used as HA consistency authority.

The HA profile instead requires shared SQL schema/migration metadata that allows standby startup/readiness to prove:

- the required schema exists completely;
- the schema revision is supported by the running release;
- no partial/failed migration is being treated as ready;
- initialization is idempotent and restart-safe.

The promoted process must fail closed when this proof cannot be made.

## Initial implementation order

The capability map implies the following implementation order for #956:

1. shared PostgreSQL kernel repository, including event/idempotency/revision semantics;
2. shared security repositories needed for authentication/session/revocation, authorization and approvals;
3. shared Automation persistence;
4. shared distributed runtime persistence for reservations/dispatch reconciliation;
5. shared scopes/File/Workspace metadata and Run/Workspace bindings;
6. HA schema/bootstrap/readiness compatibility state;
7. shared File/Workspace content provider composition or explicit capability gating;
8. conditional feature adapters only when they are promoted into the supported HA capability set.

This order is narrower than porting every SQLite/JSON store and directly follows the #566 acceptance scenarios.

## Readiness rule

An HA instance is not write-ready merely because it owns the coordination lease. Before `PROMOTING` can become `ACTIVE`, it must prove that every `required` state source is available with its required disposition and that every enabled `conditional` capability has either a safe shared implementation or an effective fail-closed gate.

That readiness proof belongs to the HA composition; individual domain services continue to depend on platform-owned repository/provider contracts rather than database handles.

## Drift prevention

`tests/architecture/test_ha_durable_state_capability_map.py` enforces that:

- every `SINGLE_NODE_DURABLE_STORES` entry is classified;
- duplicate capability-map IDs are rejected;
- required HA state cannot be classified as unsupported/reconstructable;
- unsupported state always names a fail-closed gate;
- the known `model-routing-profiles` inventory mismatch stays explicit until the underlying backup inventory is corrected;
- provider/volatile pseudo-state additions are intentional rather than accidental inventory drift.

When the single-node persistence topology grows, CI therefore forces a deliberate HA classification instead of allowing new host-local durable state to enter unnoticed.
