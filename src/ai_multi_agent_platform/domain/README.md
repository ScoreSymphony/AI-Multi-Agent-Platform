# Canonical domain core

This package owns the platform-neutral domain vocabulary for the AI Multi-Agent Platform.

Rules:

- canonical IDs are platform-owned and globally unique;
- backend/provider IDs are external references only;
- retries create new Run identities;
- Worker Job models placement of a Run on a Worker and does not redefine Task/Run semantics;
- lifecycle changes must follow the transition tables in `lifecycle.py`;
- this package must not import Hermes, Forge, Temporal or other replaceable integration frameworks.

Cross-boundary serialization is defined separately under `schemas/domain/` so Python dataclasses are not the external wire contract.
