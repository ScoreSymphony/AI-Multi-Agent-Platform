"""Canonical durable-store inventory for the current single-node deployment profile.

The backup layer uses this inventory to distinguish a structurally complete deployment from a
folder that merely happens to contain ``db/kernel.sqlite3``.  Eager stores are created by the
single-node composition during normal construction and therefore must exist before a backup is
considered healthy.  Lazy JSON stores are optional until their owning subsystem first persists
state, but are still named here so restore validators and future profile versions have one shared
inventory rather than duplicating path knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StoreKind = Literal["sqlite", "json"]


@dataclass(frozen=True, slots=True)
class DurableStoreSpec:
    """One platform-owned durable store in the single-node data root."""

    name: str
    path: str
    kind: StoreKind
    required: bool


SINGLE_NODE_DURABLE_STORES: tuple[DurableStoreSpec, ...] = (
    DurableStoreSpec("kernel", "db/kernel.sqlite3", "sqlite", True),
    DurableStoreSpec("scopes", "db/scopes.sqlite3", "sqlite", True),
    DurableStoreSpec("files-metadata", "db/files.sqlite3", "sqlite", True),
    DurableStoreSpec("workspaces-metadata", "db/workspaces.sqlite3", "sqlite", True),
    DurableStoreSpec("verification", "db/verification.sqlite3", "sqlite", True),
    DurableStoreSpec("authentication", "db/authentication.sqlite3", "sqlite", True),
    DurableStoreSpec("authorization", "db/authorization.sqlite3", "sqlite", True),
    DurableStoreSpec("automation", "db/automation.sqlite3", "sqlite", True),
    DurableStoreSpec("agents", "db/agents.json", "json", False),
    DurableStoreSpec("conversations", "db/conversations.json", "json", False),
    DurableStoreSpec("models", "db/models.json", "json", False),
    DurableStoreSpec("model-providers", "db/model-providers.json", "json", False),
    DurableStoreSpec("onboarding-commands", "db/onboarding-commands.json", "json", False),
)

REQUIRED_SINGLE_NODE_DURABLE_PATHS = frozenset(
    store.path for store in SINGLE_NODE_DURABLE_STORES if store.required
)
KNOWN_SINGLE_NODE_DURABLE_PATHS = frozenset(store.path for store in SINGLE_NODE_DURABLE_STORES)


def single_node_store(name: str) -> DurableStoreSpec:
    """Return one named store or fail loudly when the profile inventory and caller drift apart."""

    for store in SINGLE_NODE_DURABLE_STORES:
        if store.name == name:
            return store
    raise KeyError(name)
