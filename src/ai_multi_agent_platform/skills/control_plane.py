"""Registration seam for canonical Skill Control Plane resources and commands."""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.control_plane.extensions import ControlPlane

from .control_plane_lifecycle import SkillLifecycleCommands
from .control_plane_resources import (
    SKILL_BINDING_COLLECTION,
    SKILL_BUNDLE_COLLECTION,
    SKILL_COLLECTION,
    SkillBindingResourceService,
    SkillBundleResourceService,
    SkillResourceService,
)
from .control_plane_runtime import (
    SkillExecutionEnvironment,
    SkillExecutionEnvironmentResolver,
    SkillRuntimeCommands,
)
from .runtime import SkillExecutionCoordinator
from .service import SkillService

SKILL_COMMANDS = (
    "skill.create",
    "skill.update",
    "skill.clone",
    "skill.enable",
    "skill.disable",
    "skill.deprecate",
    "skill.trust",
    "skill.resolve",
    "skill.bind-agent-run",
)


def register_skill_control_plane(
    control_plane: ControlPlane,
    service: SkillService,
    *,
    coordinator: SkillExecutionCoordinator | None = None,
    agents: AgentService | None = None,
    execution_environment_resolver: SkillExecutionEnvironmentResolver | None = None,
) -> None:
    lifecycle = SkillLifecycleCommands(service)
    control_plane.register_resource_service(SKILL_COLLECTION, SkillResourceService(service))
    control_plane.register_resource_service(
        SKILL_BUNDLE_COLLECTION,
        SkillBundleResourceService(service),
    )
    control_plane.register_resource_service(
        SKILL_BINDING_COLLECTION,
        SkillBindingResourceService(service),
    )
    for command, handler in (
        ("skill.create", lifecycle.create),
        ("skill.update", lifecycle.update),
        ("skill.clone", lifecycle.clone),
        ("skill.enable", lifecycle.enable),
        ("skill.disable", lifecycle.disable),
        ("skill.deprecate", lifecycle.deprecate),
        ("skill.trust", lifecycle.transition_trust),
    ):
        control_plane.register_command(command, handler)

    if coordinator is None or agents is None:
        return
    runtime = SkillRuntimeCommands(
        coordinator,
        agents,
        execution_environment_resolver=execution_environment_resolver,
    )
    control_plane.register_command("skill.resolve", runtime.resolve)
    control_plane.register_command("skill.bind-agent-run", runtime.bind_agent_run)


__all__ = [
    "SKILL_BINDING_COLLECTION",
    "SKILL_BUNDLE_COLLECTION",
    "SKILL_COLLECTION",
    "SKILL_COMMANDS",
    "SkillExecutionEnvironment",
    "SkillExecutionEnvironmentResolver",
    "register_skill_control_plane",
]
