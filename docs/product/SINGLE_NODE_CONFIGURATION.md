# Single-node configuration UX

Issue #696 productizes the existing canonical platform configuration for the supported single-node topology.

## Scope

This configuration experience targets one Control Plane and local platform resources. It deliberately does not add multi-host enrollment, remote Worker bootstrap, cluster networking, distributed fleet administration or HA configuration.

The browser boundary remains:

```text
Browser UI
  -> /api/v1 Control Plane
    -> canonical owner domain
      -> replaceable provider/adapter
```

The frontend never becomes configuration authority and never calls model runtimes, capability providers, Workers, connector backends, plugin registries or other provider-private services directly.

## Configuration surfaces

Configuration stays in the owning product area instead of being duplicated into a second settings store.

### Agents

The Agents surface supports purpose-built create, edit and clone workflows over canonical Agent commands. The editor exposes supported canonical intent for:

- identity, role and enabled state;
- inline/reference instruction source;
- Project and Workspace scope;
- explicit model or exact Model Routing Profile reference;
- provider-neutral model requirements;
- allowed, required and denied capabilities;
- Memory scopes and configuration references;
- Knowledge source references;
- authorization and verification policy references.

Existing advanced capability constraints that the focused editor does not rewrite are preserved. Agent runtime evidence remains separate from editable definition state.

Agent updates use the loaded `current_revision` as `expected_revision`; successful edits therefore create the canonical next immutable Agent revision rather than rewriting history.

### Agent Teams

The Agent Teams surface supports create and edit workflows for:

- exact pinned member Agent revisions;
- member roles and required/optional state;
- delegation relationships;
- leader selection;
- shared capabilities and resource references;
- supported runtime limits;
- Project and Workspace scope;
- coordination policy references.

Newly selected members are pinned to the exact current Agent revision shown by the canonical inventory.

### Models and routing

The Models surface continues to expose model/provider inventory, health and lifecycle operations and now also exposes first-class Model Routing Profiles.

Routing Profile editing uses the existing canonical `model-routing-profile.create`, `model-routing-profile.version`, `model-routing-profile.enable` and `model-routing-profile.disable` commands. Profile revisions remain immutable. Project scope is fixed after creation.

The editor exposes provider-neutral requirements and preferred model selection. Provider-private configuration is not promoted into canonical routing identity merely for UI convenience.

### Tools and Capability Assignments

The Tools surface continues to expose Capability/Provider inventory and safety metadata and now also exposes first-class Capability Assignment configuration.

Capability Assignments model canonical required/allowed/denied capability policy for an Agent, Agent Team or Project. #696 adds narrow northbound create/revise commands over the existing owning service. Those commands preserve normal authorization/approval semantics, optimistic revision binding and server-authored provenance.

The browser cannot author trusted provenance. Any client-supplied provenance is discarded before the owning service receives canonical content.

### Existing lifecycle configuration

The following mature product surfaces remain the owning configuration paths rather than being duplicated by #696:

- Memory and Knowledge lifecycle;
- Automations;
- Connector/Connection lifecycle;
- Plugins;
- Projects and Workspaces;
- Templates;
- supported local Compute/Worker controls;
- Approvals and security decisions.

Agent/Team editors consume canonical inventories from those domains through selectors where their contracts expose safe references.

## Shared editor behavior

The focused editors use common configuration primitives for:

- canonical resource selection instead of ordinary copied-ID entry;
- explicit preservation of referenced resources missing from the current inventory;
- dirty-state indication;
- discard behavior;
- browser navigation warning for unsaved changes;
- canonical error presentation;
- responsive form layout;
- immutable revision-aware save semantics.

Unavailable optional inventories degrade their selectors without creating private provider fallbacks.

## Security and trust

Browser controls are never authorization boundaries. The Control Plane and owning service remain authoritative for permission checks, approval requirements, revision conflicts and audit semantics.

Secret values are not introduced into these editors. Existing Connection and Plugin secret/write-only handling remains authoritative.

## Single-node boundary

#696 does not introduce configuration for:

- adding or pairing another machine;
- remote Worker installation or enrollment;
- multi-host credentials/trust bootstrap;
- private-tunnel or network topology;
- distributed placement administration;
- multiple Control Planes or HA;
- remote accelerator fleets.

Those require separate product work and must not become hidden prerequisites for configuring or running one machine.
