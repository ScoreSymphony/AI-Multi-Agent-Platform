"""Single-node Research composition prepared for aggregate #589 integration."""

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
    """Attach durable Search-aware Research to an already-built single-node deployment.

    This is intentionally an additive composition seam for the active-branch integration window.
    The later aggregate branch can fold the same three operations directly into
    ``build_single_node_deployment`` and expose the service on ``SingleNodeDeployment`` once all
    concurrent central-file changes have been combined.
    """

    path = database_path or deployment.config.database_dir / "research.sqlite3"
    research = ResearchService(
        SqliteResearchRepository(path),
        authorization=deployment.approval_gate,
    )
    register_searchable_research_control_plane(deployment.control_plane, research)
    return SingleNodeResearchComposition(service=research, database_path=path)


__all__ = ["SingleNodeResearchComposition", "compose_single_node_research"]
