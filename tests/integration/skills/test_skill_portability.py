"""Skill portability integration coverage.

Migrated from the historical Issue #588 root-level suite as part of #722.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.skill_codecs import (
    SKILL_BUNDLE_RESOURCE_TYPE,
    SKILL_RESOURCE_TYPE,
    SkillPortableSnapshot,
    register_skill_portability_codecs,
    snapshot_skill,
)
from ai_multi_agent_platform.portability.skill_import import SkillImportMutationHandler
from ai_multi_agent_platform.skills import (
    InMemorySkillRepository,
    SkillContent,
    SkillProfile,
    SkillResolutionRequest,
    SkillResolver,
    SkillRevisionRef,
    SkillService,
)

OWNER = OwnerRef(type="user", id="issue-588-test")


def _profile(name: str, content: str) -> SkillProfile:
    return SkillProfile(
        name=name,
        purpose_categories=("test",),
        content=SkillContent(content=content),
    )


def _request(
    *,
    agent_id: str,
    run_id: str,
    task_id: str,
    explicit: tuple[SkillRevisionRef, ...] = (),
) -> SkillResolutionRequest:
    return SkillResolutionRequest(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        agent_revision=1,
        agent_role="worker",
        explicit_skills=explicit,
    )


def test_portability_preserves_skill_revision_history_and_provenance() -> None:
    source_repository = InMemorySkillRepository()
    source_service = SkillService(source_repository)
    first = source_service.create_skill(
        _profile("Portable", "one"),
        owner_ref=OWNER,
        provenance=Provenance(source="source-create", details={"origin": "fixture"}),
    )
    second = source_service.update_skill(
        first.skill_id,
        replace(first.profile, content=SkillContent(content="two")),
        expected_revision=1,
        provenance=Provenance(source="source-update", details={"origin": "fixture"}),
    )
    serializers = ResourceSerializerRegistry()
    register_skill_portability_codecs(serializers)
    resource = serializers.serialize(
        SKILL_RESOURCE_TYPE, snapshot_skill(source_repository, first.skill_id)
    )
    decoded = serializers.deserialize(resource)

    assert isinstance(decoded, SkillPortableSnapshot)
    assert decoded.definition.current_revision == 2
    assert tuple(item.revision for item in decoded.revisions) == (1, 2)
    assert decoded.revisions[0].provenance == first.provenance
    assert decoded.revisions[1].provenance == second.provenance

    destination = InMemorySkillRepository()
    handler = SkillImportMutationHandler(destination)
    context = ImportContext()
    asyncio.run(handler.preflight(resource, decoded, context))
    token = asyncio.run(handler.apply(resource, decoded, context))
    assert token == first.skill_id
    assert destination.list_skill_revisions(first.skill_id) == decoded.revisions

    bundle = SkillResolver(source_repository).resolve(
        _request(
            agent_id=new_id("agent"),
            run_id=new_id("run"),
            task_id=new_id("task"),
            explicit=(second.ref,),
        )
    )
    bundle_resource = serializers.serialize(SKILL_BUNDLE_RESOURCE_TYPE, bundle)
    restored_bundle = serializers.deserialize(bundle_resource)
    assert restored_bundle == bundle
