from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateTrust,
    TemplateType,
)

PASSWORD = "correct horse battery staple"


def _context(user_id: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id="issue-78-trust-control-plane",
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
        idempotency_key=key,
    )


def test_single_node_requires_explicit_activation_before_untrusted_template_apply(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        draft = deployment.templates.templates.create_draft(
            owner_ref=owner,
            content=TemplateContent(
                name="Imported composite",
                description="Untrusted imported configuration",
                template_type=TemplateType.COMPOSITE,
                configuration=TemplateConfiguration(payload={}),
                provenance=TemplateProvenance(
                    author="remote-author",
                    source="portable-import",
                    trust=TemplateTrust.UNTRUSTED,
                ),
            ),
        )
        published = deployment.templates.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        with pytest.raises(ContractError) as blocked:
            await deployment.control_plane.execute_command(
                _context(admin.user_id, "apply-before-activation"),
                "template.apply",
                published.template_id,
                {"revision": published.revision},
            )
        assert blocked.value.code is ErrorCode.FORBIDDEN
        assert deployment.templates.repository.list_instantiations(published.template_id) == ()

        activated_resource = await deployment.control_plane.execute_command(
            _context(admin.user_id, "activate-untrusted-template"),
            "template.publish",
            published.template_id,
            {
                "expected_revision": published.revision,
                "activate_untrusted": True,
            },
        )
        activated = deployment.templates.repository.get_revision(
            published.template_id,
            published.revision + 1,
        )
        assert activated_resource["current_revision"] == activated.revision
        assert activated.content.provenance.trust is TemplateTrust.TRUSTED
        assert activated.content.provenance.source_template == published.ref
        assert (
            deployment.templates.repository.get_revision(
                published.template_id,
                published.revision,
            ).content.provenance.trust
            is TemplateTrust.UNTRUSTED
        )

        instance_resource = await deployment.control_plane.execute_command(
            _context(admin.user_id, "apply-after-activation"),
            "template.apply",
            published.template_id,
            {"revision": activated.revision},
        )
        assert instance_resource["source"] == {
            "template_id": published.template_id,
            "revision": activated.revision,
        }
        assert len(deployment.templates.repository.list_instantiations(published.template_id)) == 1

    asyncio.run(scenario())
