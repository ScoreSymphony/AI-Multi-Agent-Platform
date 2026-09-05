"""Authoritative durable-store inventory for the single-node deployment profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StoreKind = Literal["sqlite", "json"]


@dataclass(frozen=True, slots=True)
class DurableStoreSpec:
    """One platform-owned durable store that participates in backup/restore."""

    store_id: str
    path: str
    kind: StoreKind
    required: bool
    owner: str


SINGLE_NODE_DURABLE_STORES: tuple[DurableStoreSpec, ...] = (
    DurableStoreSpec("kernel", "db/kernel.sqlite3", "sqlite", True, "kernel"),
    DurableStoreSpec("scopes", "db/scopes.sqlite3", "sqlite", True, "control-plane"),
    DurableStoreSpec("files", "db/files.sqlite3", "sqlite", True, "data"),
    DurableStoreSpec("workspaces", "db/workspaces.sqlite3", "sqlite", True, "workspaces"),
    DurableStoreSpec(
        "run-workspace-bindings",
        "db/run-workspace-bindings.sqlite3",
        "sqlite",
        True,
        "workspaces",
    ),
    DurableStoreSpec("verification", "db/verification.sqlite3", "sqlite", True, "verification"),
    DurableStoreSpec("evaluation", "db/evaluation.sqlite3", "sqlite", True, "evaluation"),
    DurableStoreSpec("authentication", "db/authentication.sqlite3", "sqlite", True, "security"),
    DurableStoreSpec("authorization", "db/authorization.sqlite3", "sqlite", True, "security"),
    DurableStoreSpec("automation", "db/automation.sqlite3", "sqlite", True, "automation"),
    DurableStoreSpec("notifications", "db/notifications.sqlite3", "sqlite", True, "notifications"),
    DurableStoreSpec("agents", "db/agents.json", "json", False, "agents"),
    DurableStoreSpec("conversations", "db/conversations.json", "json", False, "conversations"),
    DurableStoreSpec("models", "db/models.json", "json", False, "models"),
    DurableStoreSpec("model-providers", "db/model-providers.json", "json", False, "onboarding"),
    DurableStoreSpec(
        "onboarding-commands", "db/onboarding-commands.json", "json", False, "onboarding"
    ),
    DurableStoreSpec("templates", "db/templates.json", "json", False, "templates"),
    DurableStoreSpec(
        "capability-assignments",
        "db/capability-assignments.json",
        "json",
        False,
        "capability-assignments",
    ),
)


def required_single_node_store_paths() -> tuple[str, ...]:
    """Return every store that must exist in an initialized single-node data root."""

    return tuple(spec.path for spec in SINGLE_NODE_DURABLE_STORES if spec.required)


def optional_single_node_store_paths() -> tuple[str, ...]:
    """Return lazy stores that are backed up whenever they exist."""

    return tuple(spec.path for spec in SINGLE_NODE_DURABLE_STORES if not spec.required)
