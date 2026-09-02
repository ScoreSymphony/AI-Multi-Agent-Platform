# Canonical domain core

This package owns the platform-neutral domain vocabulary for the AI Multi-Agent Platform.

Rules:

- canonical IDs are platform-owned, globally unique and immutable after entity creation;
- canonical relationship fields reference canonical IDs and reject backend/provider-private identifiers;
- backend/provider IDs are external references only;
- retries create new Run identities;
- Worker Job models placement of a Run on a Worker and does not redefine Task/Run semantics;
- lifecycle-bearing entities are immutable value objects: status changes use `transition_to(...)`, which validates `lifecycle.py` before returning the next state;
- direct status reassignment cannot bypass lifecycle rules;
- nested domain configuration/metadata collections are defensively deep-frozen, not only top-level dataclass fields;
- Event payload/provenance data is defensively deep-frozen so append-only facts cannot be rewritten through retained mutable objects;
- Agent carries provider-neutral model/capability references plus a structured policy-requirements hook without selecting a final authorization engine;
- Model Assignment supports canonical Agent, Task, Step, Capability and Policy Scope targets;
- Policy Scope is only a canonical model-assignment target/scope identity, not the final authorization-policy model;
- Result carries an outcome plus immutable structured status data and Artifact references;
- this package must not import Hermes, Forge, Temporal or other replaceable integration frameworks.

Cross-boundary serialization is defined separately under `schemas/domain/` so Python dataclasses are not the external wire contract.
