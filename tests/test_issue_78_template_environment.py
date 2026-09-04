from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates.environment import PlatformTemplateEnvironmentResolver
from ai_multi_agent_platform.workspaces import LocalWorkspaceProvider, WorkspaceType


def test_environment_resolver_uses_only_matching_server_owned_inventory(tmp_path: Path) -> None:
    async def scenario() -> None:
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        owner = OwnerRef(type="user", id="user:owner")
        other = OwnerRef(type="user", id="user:other")
        project_id = new_id("project")
        other_project_id = new_id("project")
        owned = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=owner,
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=DataAccessContext(
                operation=OperationContext(
                    correlation_id="environment-owned",
                    owner_type=owner.type,
                    owner_id=owner.id,
                    project_id=project_id,
                ),
                actor_ref=owner.id,
            ),
        )
        foreign = await workspaces.create_workspace(
            project_id=other_project_id,
            owner_ref=other,
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=DataAccessContext(
                operation=OperationContext(
                    correlation_id="environment-foreign",
                    owner_type=other.type,
                    owner_id=other.id,
                    project_id=other_project_id,
                ),
                actor_ref=other.id,
            ),
        )
        context = RequestContext(
            request_id="template-environment",
            correlation_id="template-environment",
            actor=ActorContext(
                principal_ref=owner.id,
                owner_type=owner.type,
                owner_id=owner.id,
            ),
        )
        resolver = PlatformTemplateEnvironmentResolver(
            workspaces=workspaces,
            capabilities=lambda: ("capability.alpha",),
            plugins=lambda: ("plugin.alpha",),
            connectors=lambda: ("connector.alpha",),
            model_policies=lambda: ("model-policy.alpha",),
            grantable_permissions=lambda request: (
                "files.read",
                f"owner:{request.actor.owner_id}",
            ),
            placeholders=lambda _: ("project_name",),
            secret_reference_placeholders=lambda _: ("model_credential_ref",),
            validated_configuration_refs=lambda _: ("config://approved",),
        )

        environment = await resolver.resolve(context)

        assert environment.capability_ids == frozenset({"capability.alpha"})
        assert environment.plugin_ids == frozenset({"plugin.alpha"})
        assert environment.connector_ids == frozenset({"connector.alpha"})
        assert environment.model_policy_refs == frozenset({"model-policy.alpha"})
        assert environment.grantable_permissions == frozenset(
            {"files.read", f"owner:{owner.id}"}
        )
        assert environment.workspace_prerequisites == frozenset({owned.id})
        assert foreign.id not in environment.workspace_prerequisites
        assert environment.resolved_placeholders == frozenset({"project_name"})
        assert environment.resolved_secret_reference_placeholders == frozenset(
            {"model_credential_ref"}
        )
        assert environment.validated_configuration_refs == frozenset({"config://approved"})

    asyncio.run(scenario())


def test_environment_resolver_is_conservative_for_unconfigured_inventories() -> None:
    async def scenario() -> None:
        resolver = PlatformTemplateEnvironmentResolver()
        environment = await resolver.resolve(
            RequestContext(
                request_id="template-empty-environment",
                correlation_id="template-empty-environment",
                actor=ActorContext(principal_ref="user:owner"),
            )
        )
        assert environment.capability_ids == frozenset()
        assert environment.plugin_ids == frozenset()
        assert environment.connector_ids == frozenset()
        assert environment.model_policy_refs == frozenset()
        assert environment.grantable_permissions == frozenset()
        assert environment.workspace_prerequisites == frozenset()

    asyncio.run(scenario())


def test_environment_resolver_rejects_invalid_inventory_ids() -> None:
    async def scenario() -> None:
        resolver = PlatformTemplateEnvironmentResolver(capabilities=lambda: ("",))
        with pytest.raises(ValueError, match="non-blank"):
            await resolver.resolve(
                RequestContext(
                    request_id="template-invalid-environment",
                    correlation_id="template-invalid-environment",
                    actor=ActorContext(principal_ref="user:owner"),
                )
            )

    asyncio.run(scenario())
