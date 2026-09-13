from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorType, AuthorizationAction, LocalPrincipalPolicy
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateRequirements,
    TemplateType,
)

PASSWORD = "correct horse battery staple"


def test_single_node_template_permissions_resolve_from_live_admin_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        draft = deployment.templates.templates.create_draft(
            owner_ref=owner,
            content=TemplateContent(
                name="Permission environment",
                description="Requires a canonical action granted by the local admin policy",
                template_type=TemplateType.COMPOSITE,
                configuration=TemplateConfiguration(payload={}),
                requirements=TemplateRequirements(
                    permission_actions=(AuthorizationAction.EXECUTE.value,),
                ),
                provenance=TemplateProvenance(author="test", source="test"),
            ),
        )
        published = deployment.templates.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        context = RequestContext(
            request_id="template-permission-preview",
            correlation_id="template-permission-preview",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
                actor_type=ActorType.HUMAN.value,
            ),
            idempotency_key="template-permission-preview",
        )

        preview = await deployment.control_plane.execute_command(
            context,
            "template.preview",
            published.template_id,
            {},
        )

        assert preview["applicable"] is True
        assert preview["ungrantable_permissions"] == []

    asyncio.run(scenario())


def test_sqlite_permission_enumeration_is_conservative_for_scoped_and_approval_policies(
    tmp_path: Path,
) -> None:
    provider = SqliteLocalAuthorizationProvider(tmp_path / "authorization.sqlite3")
    scoped_ref = "user:scoped"
    provider.register(
        LocalPrincipalPolicy(
            principal_ref=scoped_ref,
            actor_types=frozenset({ActorType.HUMAN}),
            allowed_actions=frozenset({AuthorizationAction.EXECUTE}),
            project_ids=frozenset({new_id("project")}),
        )
    )
    approval_ref = "user:approval"
    provider.register(
        LocalPrincipalPolicy(
            principal_ref=approval_ref,
            actor_types=frozenset({ActorType.HUMAN}),
            allowed_actions=frozenset({AuthorizationAction.READ}),
            approval_actions=frozenset({AuthorizationAction.EXECUTE}),
        )
    )

    assert (
        provider.globally_grantable_actions(
            scoped_ref,
            actor_type=ActorType.HUMAN.value,
        )
        == frozenset()
    )
    assert provider.globally_grantable_actions(
        approval_ref,
        actor_type=ActorType.HUMAN.value,
    ) == frozenset({AuthorizationAction.READ})
    assert provider.globally_grantable_actions(approval_ref, actor_type=None) == frozenset()
    assert (
        provider.globally_grantable_actions(
            approval_ref,
            actor_type=ActorType.SERVICE.value,
        )
        == frozenset()
    )
