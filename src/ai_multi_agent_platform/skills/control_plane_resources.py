"""Read-only Control Plane projections for Skills, Bundles and Run bindings."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .codec import skill_binding_to_json, skill_bundle_to_json, skill_revision_to_json
from .models import SkillBundle, SkillRunBinding
from .service import SkillService

SKILL_COLLECTION = "skills"
SKILL_BUNDLE_COLLECTION = "skill-bundles"
SKILL_BINDING_COLLECTION = "skill-bindings"


class SkillResourceService:
    def __init__(self, service: SkillService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            skill_resource(self.service, item.skill_id)
            for item in self.service.repository.list_skills()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return skill_resource(self.service, resource_id)


class SkillBundleResourceService:
    def __init__(self, service: SkillService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(bundle_resource(item) for item in self.service.repository.list_bundles())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return bundle_resource(self.service.repository.get_bundle(resource_id))


class SkillBindingResourceService:
    def __init__(self, service: SkillService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(binding_resource(item) for item in self.service.repository.list_bindings())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return binding_resource(self.service.repository.get_binding(resource_id))


def skill_resource(service: SkillService, skill_id: str) -> dict[str, JsonValue]:
    definition = service.repository.get_skill(skill_id)
    revision = service.repository.get_skill_revision(skill_id, definition.current_revision)
    return {
        "id": skill_id,
        "type": "skill",
        "current_revision": definition.current_revision,
        "project_id": definition.project_id,
        "workspace_id": definition.workspace_id,
        "owner_ref": {"type": definition.owner_ref.type, "id": definition.owner_ref.id},
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "revision": skill_revision_to_json(revision),
    }


def bundle_resource(bundle: SkillBundle) -> dict[str, JsonValue]:
    return {"id": bundle.skill_bundle_id, "type": "skill_bundle", **skill_bundle_to_json(bundle)}


def binding_resource(binding: SkillRunBinding) -> dict[str, JsonValue]:
    return {"id": binding.binding_id, "type": "skill_binding", **skill_binding_to_json(binding)}
