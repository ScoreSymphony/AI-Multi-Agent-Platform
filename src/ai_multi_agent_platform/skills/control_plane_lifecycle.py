"""Lifecycle mutation handlers for canonical Skills."""

from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext

from .codec import revision_ref_from_json, skill_profile_from_json
from .control_plane_helpers import (
    control_plane_provenance,
    mapping,
    optional_positive_int,
    optional_string,
    owner_ref,
    positive_int,
    provided_owner_ref,
    require_collection,
    required,
    required_string,
)
from .control_plane_resources import SKILL_COLLECTION, skill_resource
from .models import SkillEvaluationStatus, SkillTrustStatus
from .service import SkillService


class SkillLifecycleCommands:
    def __init__(self, service: SkillService) -> None:
        self.service = service

    async def create(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        require_collection(resource_ref, SKILL_COLLECTION)
        revision = self.service.create_skill(
            skill_profile_from_json(required(payload, "profile")),
            owner_ref=owner_ref(payload.get("owner_ref"), context),
            project_id=optional_string(payload, "project_id"),
            workspace_id=optional_string(payload, "workspace_id"),
            provenance=control_plane_provenance(context, "skill.create"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = self.service.repository.get_skill(resource_ref)
        project_id = (
            current.project_id
            if "project_id" not in payload
            else optional_string(payload, "project_id")
        )
        workspace_id = (
            current.workspace_id
            if "workspace_id" not in payload
            else optional_string(payload, "workspace_id")
        )
        revision = self.service.update_skill(
            resource_ref,
            skill_profile_from_json(required(payload, "profile")),
            expected_revision=positive_int(payload, "expected_revision"),
            owner_ref=provided_owner_ref(payload.get("owner_ref")),
            project_id=project_id,
            workspace_id=workspace_id,
            provenance=control_plane_provenance(context, "skill.update"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def clone(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        source = self.service.get_skill_revision(
            resource_ref,
            optional_positive_int(payload, "revision"),
        )
        revision = self.service.clone_skill(
            resource_ref,
            revision=source.revision,
            owner_ref=provided_owner_ref(payload.get("owner_ref")),
            project_id=(
                source.project_id
                if "project_id" not in payload
                else optional_string(payload, "project_id")
            ),
            workspace_id=(
                source.workspace_id
                if "workspace_id" not in payload
                else optional_string(payload, "workspace_id")
            ),
            name=optional_string(payload, "name"),
            provenance=control_plane_provenance(context, "skill.clone"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def enable(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = self.service.set_enabled(
            resource_ref,
            True,
            expected_revision=positive_int(payload, "expected_revision"),
            provenance=control_plane_provenance(context, "skill.enable"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def disable(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = self.service.set_enabled(
            resource_ref,
            False,
            expected_revision=positive_int(payload, "expected_revision"),
            provenance=control_plane_provenance(context, "skill.disable"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def deprecate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = self.service.deprecate_skill(
            resource_ref,
            expected_revision=positive_int(payload, "expected_revision"),
            replacement=revision_ref_from_json(payload.get("replacement")),
            provenance=control_plane_provenance(context, "skill.deprecate"),
        )
        return skill_resource(self.service, revision.skill_id)

    async def transition_trust(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        target = SkillTrustStatus(required_string(payload, "target"))
        evaluation_raw = optional_string(payload, "evaluation_status")
        metadata_raw = payload.get("evaluation_metadata")
        evaluation_metadata = (
            None
            if metadata_raw is None
            else cast(dict[str, JsonValue], dict(mapping(metadata_raw, "evaluation_metadata")))
        )
        revision = self.service.transition_trust(
            resource_ref,
            target,
            expected_revision=positive_int(payload, "expected_revision"),
            evaluation_status=(
                SkillEvaluationStatus(evaluation_raw) if evaluation_raw is not None else None
            ),
            evaluation_metadata=evaluation_metadata,
            provenance=control_plane_provenance(context, "skill.trust"),
        )
        return skill_resource(self.service, revision.skill_id)
