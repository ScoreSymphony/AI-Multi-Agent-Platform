"""First-run path value objects shared by onboarding workflows."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue


@dataclass(frozen=True, slots=True)
class FirstRunPath:
    """One canonical Project/Workspace/General-Assistant execution path."""

    project_id: str
    workspace_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class FirstRunPathProjection:
    """Structural and executable first-run paths derived without side effects."""

    project_ids: tuple[str, ...]
    workspace_bindings: tuple[tuple[str, str], ...]
    structural_paths: tuple[FirstRunPath, ...]
    executable_paths: tuple[FirstRunPath, ...]
    blockers: tuple[dict[str, JsonValue], ...]
