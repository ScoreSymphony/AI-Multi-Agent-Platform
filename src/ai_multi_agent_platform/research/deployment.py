"""Compatibility access to the standard single-node Research composition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .repository import SqliteResearchRepository
from .search import register_searchable_research_control_plane
from .service import ResearchService

if TYPE_CHECKING:
    from ai_multi_agent_platform.deployment.single_node import SingleNodeDeployment


@dataclass(frozen=True, slots=True)
class SingleNodeResearchComposition:
    """Durable Research service attached to one standard single-node deployment."""

    service: ResearchService
    database_path: Path


def compose_single_node_research(
    deployment: SingleNodeDeployment,
    *,
    database_path: Path | None = None,
) -> SingleNodeResearchComposition:
    """Return standard Research or attach an explicitly separate compatibility store.

    The ordinary ``build_single_node_deployment`` path now owns ``research.sqlite3`` and Search
    registration. Calling this helper without a custom path is therefore idempotent. A custom path
    remains available for isolated compatibility/test compositions and is registered separately.
    """

    standard_path = deployment.config.database_dir / "research.sqlite3"
    if database_path is None or database_path == standard_path:
        return SingleNodeResearchComposition(
            service=deployment.research,
            database_path=standard_path,
        )

    research = ResearchService(
        SqliteResearchRepository(database_path),
        authorization=deployment.approval_gate,
    )
    register_searchable_research_control_plane(deployment.control_plane, research)
    return SingleNodeResearchComposition(service=research, database_path=database_path)


__all__ = ["SingleNodeResearchComposition", "compose_single_node_research"]
