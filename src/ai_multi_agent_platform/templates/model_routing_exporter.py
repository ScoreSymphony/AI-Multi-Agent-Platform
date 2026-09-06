"""Create reusable Templates from canonical durable model-routing profiles."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRoutingProfileRef, ModelRoutingProfileService
from ai_multi_agent_platform.models.routing_profiles import ModelRoutingProfileRevision

from .models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService


@dataclass(slots=True)
class ModelRoutingPolicyTemplateExporter:
    """Export one authorized #309 profile revision as provider-neutral Template intent."""

    profiles: ModelRoutingProfileService
    templates: TemplateService

    async def create_from_profile(
        self,
        profile_id: str,
        *,
        principal_ref: str,
        actor_type: str | None,
        correlation_id: str,
        causation_id: str,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        definition = self.profiles.repository.get_definition(profile_id)
        source_revision = definition.current_revision if revision is None else revision
        source = await self.profiles.get_revision(
            ModelRoutingProfileRef(profile_id, source_revision),
            principal_ref=principal_ref,
            actor_type=actor_type,
            context=OperationContext(
                correlation_id=correlation_id,
                causation_id=causation_id,
                owner_type=definition.owner_ref.type,
                owner_id=definition.owner_ref.id,
                project_id=definition.project_id,
            ),
        )
        content = TemplateContent(
            name=name or source.name,
            description=source.description,
            template_type=TemplateType.MODEL_ROUTING_POLICY,
            configuration=TemplateConfiguration(payload=_configuration_payload(source)),
            provenance=TemplateProvenance(
                author=author,
                source="canonical-model-routing-profile-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "model_routing_profile",
                    "source_resource_id": source.profile_id,
                    "source_resource_revision": source.revision,
                    "source_project_id": source.project_id,
                    "source_schema_version": source.schema_version,
                },
            ),
            tags=("model-routing-policy", "exported"),
        )
        return self.templates.create_draft(owner_ref=owner_ref, content=content)


def _configuration_payload(source: ModelRoutingProfileRevision) -> dict[str, FrozenJsonValue]:
    requirements = source.policy.requirements
    return {
        "policy": {
            "requirements": {
                "explicit_model_id": requirements.explicit_model_id,
                "min_context_window": requirements.min_context_window,
                "tool_calling": requirements.tool_calling,
                "structured_output": requirements.structured_output,
                "streaming": requirements.streaming,
                "modalities": tuple(requirements.modalities),
                "reasoning": tuple(requirements.reasoning),
                "local_only": requirements.local_only,
                "self_hosted_only": requirements.self_hosted_only,
            },
            "preferred_model_ids": tuple(source.policy.preferred_model_ids),
            "fallback": source.policy.fallback.value,
        }
    }
