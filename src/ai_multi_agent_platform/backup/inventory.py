"""Canonical durable-store inventory for the shipped single-node deployment profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StoreKind = Literal["sqlite", "json"]

SINGLE_NODE_STORE_CONTRACT_VERSION = 2


@dataclass(frozen=True, slots=True)
class StoreSpec:
    path: str
    kind: StoreKind
    introduced_contract_version: int
    required: bool = True


_SINGLE_NODE_STORE_SPECS = (
    StoreSpec("db/kernel.sqlite3", "sqlite", 1),
    StoreSpec("db/authentication.sqlite3", "sqlite", 1),
    StoreSpec("db/agents.sqlite3", "sqlite", 1),
    StoreSpec("db/projects.sqlite3", "sqlite", 1),
    StoreSpec("db/workspaces.sqlite3", "sqlite", 1),
    StoreSpec("db/files.sqlite3", "sqlite", 1),
    StoreSpec("db/secrets.sqlite3", "sqlite", 1),
    StoreSpec("db/memory.sqlite3", "sqlite", 1),
    StoreSpec("db/models.sqlite3", "sqlite", 1),
    StoreSpec("db/coordination.sqlite3", "sqlite", 1),
    StoreSpec("db/events.sqlite3", "sqlite", 1),
    StoreSpec("db/automations.sqlite3", "sqlite", 1),
    StoreSpec("db/notifications.sqlite3", "sqlite", 1),
    StoreSpec("db/verification.sqlite3", "sqlite", 1),
    StoreSpec("db/evaluation.sqlite3", "sqlite", 1),
    StoreSpec("db/accounting.sqlite3", "sqlite", 1),
    StoreSpec("db/conversations.json", "json", 1),
    StoreSpec("db/templates.json", "json", 1),
    StoreSpec("db/learning.sqlite3", "sqlite", 2),
    StoreSpec("db/learning-post-promotion.sqlite3", "sqlite", 2),
)

_OPTIONAL_SINGLE_NODE_STORE_SPECS = (
    # The distributed Control Plane composes this store only when distributed execution is
    # enabled. It is therefore not globally required by the ordinary single-node contract, but it
    # is still a first-class durable store and becomes mandatory for backups that declare the
    # distributed-runtime-state component.
    StoreSpec("db/distributed-runtime-state.json", "json", 2, required=False),
)


def required_single_node_store_specs(
    *,
    store_contract_version: int = SINGLE_NODE_STORE_CONTRACT_VERSION,
) -> tuple[StoreSpec, ...]:
    if store_contract_version < 1 or store_contract_version > SINGLE_NODE_STORE_CONTRACT_VERSION:
        raise ValueError(f"unsupported single-node store contract version: {store_contract_version}")
    return tuple(
        spec
        for spec in _SINGLE_NODE_STORE_SPECS
        if spec.required and spec.introduced_contract_version <= store_contract_version
    )


def required_single_node_store_paths(
    *,
    store_contract_version: int = SINGLE_NODE_STORE_CONTRACT_VERSION,
) -> tuple[str, ...]:
    return tuple(
        spec.path
        for spec in required_single_node_store_specs(store_contract_version=store_contract_version)
    )


def required_single_node_store_specs_added_after(
    store_contract_version: int,
) -> tuple[StoreSpec, ...]:
    if store_contract_version < 1 or store_contract_version > SINGLE_NODE_STORE_CONTRACT_VERSION:
        raise ValueError(f"unsupported single-node store contract version: {store_contract_version}")
    return tuple(
        spec
        for spec in _SINGLE_NODE_STORE_SPECS
        if spec.required and spec.introduced_contract_version > store_contract_version
    )


def optional_single_node_store_paths() -> tuple[str, ...]:
    return tuple(spec.path for spec in _OPTIONAL_SINGLE_NODE_STORE_SPECS)


__all__ = [
    "SINGLE_NODE_STORE_CONTRACT_VERSION",
    "StoreKind",
    "StoreSpec",
    "optional_single_node_store_paths",
    "required_single_node_store_paths",
    "required_single_node_store_specs",
    "required_single_node_store_specs_added_after",
]
