from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
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
    JsonSkillRepository,
    MarkdownSkillRenderer,
    ReferenceSkillRenderer,
    SkillCapabilityRequirement,
    SkillContent,
    SkillEvaluationStatus,
    SkillExecutionCoordinator,
    SkillProfile,
    SkillResolutionRequest,
    SkillResolver,
    SkillRevisionRef,
    SkillService,
    SkillSource,
    SkillTrustStatus,
    new_skill_id,
)


OWNER = OwnerRef(type="user", id="issue-588-test")


def _profile(
    name: str,
    content: str,
    *,
    dependencies: tuple[SkillRevisionRef, ...] = (),
    capability_ids: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    enabled: bool = True,
) -> SkillProfile:
    return SkillProfile(
        name=name,
        purpose_categories=("test",),
        content=SkillContent(content=content),
        dependencies=dependencies,
        capability_requirements=tuple(
            SkillCapabilityRequirement(capability_id=item) for item in capability_ids
        ),
        conflicts_with_skill_ids=conflicts,
        enabled=enabled,
    )


def _request(
    *,
    agent_id: str,
    run_id: str,
    task_id: str,
    required: tuple[SkillRevisionRef, ...] = (),
    explicit: tuple[SkillRevisionRef, ...] = (),
    defaults: tuple[SkillRevisionRef, ...] = (),
    allowed_capabilities: frozenset[str] = frozenset(),
    allow_deprecated: bool = False,
) -> SkillResolutionRequest:
    return SkillResolutionRequest(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        agent_revision=1,
        agent_role="worker",
        required_skills=required,
        explicit_skills=explicit,
        default_skills=defaults,
        allowed_capability_ids=allowed_capabilities,
        allow_deprecated=allow_deprecated,
    )


def test_skill_create_update_preserves_immutable_history() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    first = service.create_skill(
        _profile("Research method", "version one"),
        owner_ref=OWNER,
        provenance=Provenance(source="test"),
    )
    second = service.update_skill(
        first.skill_id,
        replace(first.profile, content=SkillContent(content="version two")),
        expected_revision=1,
        provenance=Provenance(source="test-update"),
    )

    assert first.revision == 1
    assert second.revision == 2
    assert repository.get_skill_revision(first.skill_id, 1) == first
    assert repository.get_skill_revision(first.skill_id, 1).profile.content.content == "version one"
    assert repository.get_skill_revision(first.skill_id, 2).profile.content.content == "version two"


def test_deterministic_minimal_resolution_expands_only_dependencies() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    dependency = service.create_skill(_profile("Dependency", "dependency"), owner_ref=OWNER)
    selected = service.create_skill(
        _profile("Selected", "selected", dependencies=(dependency.ref,)),
        owner_ref=OWNER,
    )
    unrelated = service.create_skill(_profile("Unrelated", "unrelated"), owner_ref=OWNER)
    resolver = SkillResolver(repository)
    agent_id = new_id("agent")
    run_id = new_id("run")
    task_id = new_id("task")
    request = _request(
        agent_id=agent_id,
        run_id=run_id,
        task_id=task_id,
        explicit=(selected.ref,),
    )

    first = resolver.resolve(request)
    second = resolver.resolve(request)

    assert tuple(entry.ref for entry in first.entries) == (dependency.ref, selected.ref)
    assert unrelated.ref not in tuple(entry.ref for entry in first.entries)
    assert first.digest == second.digest
    assert first.skill_bundle_id != second.skill_bundle_id


def test_missing_capability_and_scope_widening_fail_closed() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    skill = service.create_skill(
        _profile("Needs terminal", "method", capability_ids=("terminal.execute",)),
        owner_ref=OWNER,
    )
    resolver = SkillResolver(repository)
    request = _request(
        agent_id=new_id("agent"),
        run_id=new_id("run"),
        task_id=new_id("task"),
        required=(skill.ref,),
    )

    with pytest.raises(ContractError) as error:
        resolver.resolve(request)
    assert error.value.code is ErrorCode.FORBIDDEN


def test_conflicting_skills_are_rejected() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    left_id = new_skill_id()
    right_id = new_skill_id()
    left = service.create_skill(
        _profile("Left", "left", conflicts=(right_id,)),
        owner_ref=OWNER,
        skill_id=left_id,
    )
    right = service.create_skill(_profile("Right", "right"), owner_ref=OWNER, skill_id=right_id)
    resolver = SkillResolver(repository)

    with pytest.raises(ContractError) as error:
        resolver.resolve(
            _request(
                agent_id=new_id("agent"),
                run_id=new_id("run"),
                task_id=new_id("task"),
                explicit=(left.ref, right.ref),
            )
        )
    assert error.value.code is ErrorCode.CONFLICT


def test_disabled_and_deprecated_revisions_fail_closed() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    resolver = SkillResolver(repository)
    disabled = service.create_skill(_profile("Disabled", "method"), owner_ref=OWNER)
    disabled = service.set_enabled(disabled.skill_id, False, expected_revision=1)

    with pytest.raises(ContractError) as disabled_error:
        resolver.resolve(
            _request(
                agent_id=new_id("agent"),
                run_id=new_id("run"),
                task_id=new_id("task"),
                explicit=(disabled.ref,),
            )
        )
    assert disabled_error.value.code is ErrorCode.UNAVAILABLE

    deprecated = service.create_skill(_profile("Deprecated", "method"), owner_ref=OWNER)
    deprecated = service.deprecate_skill(deprecated.skill_id, expected_revision=1)
    request = _request(
        agent_id=new_id("agent"),
        run_id=new_id("run"),
        task_id=new_id("task"),
        explicit=(deprecated.ref,),
    )
    with pytest.raises(ContractError) as deprecated_error:
        resolver.resolve(request)
    assert deprecated_error.value.code is ErrorCode.UNAVAILABLE
    assert resolver.resolve(replace(request, allow_deprecated=True)).entries[0].ref == deprecated.ref


def test_task_explicit_precedes_agent_default_skill() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    explicit = service.create_skill(_profile("Explicit", "explicit"), owner_ref=OWNER)
    default = service.create_skill(_profile("Default", "default"), owner_ref=OWNER)
    bundle = SkillResolver(repository).resolve(
        _request(
            agent_id=new_id("agent"),
            run_id=new_id("run"),
            task_id=new_id("task"),
            explicit=(explicit.ref,),
            defaults=(default.ref,),
        )
    )

    assert tuple(entry.ref for entry in bundle.entries) == (explicit.ref, default.ref)


def test_bundle_digest_changes_when_effective_skill_content_changes() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    first = service.create_skill(_profile("Method", "first"), owner_ref=OWNER)
    resolver = SkillResolver(repository)
    agent_id = new_id("agent")
    run_id = new_id("run")
    task_id = new_id("task")
    first_bundle = resolver.resolve(
        _request(
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            explicit=(first.ref,),
        )
    )
    second = service.update_skill(
        first.skill_id,
        replace(first.profile, content=SkillContent(content="second")),
        expected_revision=1,
    )
    second_bundle = resolver.resolve(
        _request(
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            explicit=(second.ref,),
        )
    )

    assert first_bundle.digest != second_bundle.digest
    assert first_bundle.entries[0].content_digest != second_bundle.entries[0].content_digest


def test_adapter_replacement_preserves_canonical_bundle_identity() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    skill = service.create_skill(_profile("Method", "method"), owner_ref=OWNER)
    coordinator = SkillExecutionCoordinator(SkillResolver(repository))
    bundle, _binding = coordinator.prepare(
        _request(
            agent_id=new_id("agent"),
            run_id=new_id("run"),
            task_id=new_id("task"),
            explicit=(skill.ref,),
        )
    )

    plain = coordinator.render(bundle.skill_bundle_id, ReferenceSkillRenderer())
    markdown = coordinator.render(bundle.skill_bundle_id, MarkdownSkillRenderer())

    assert plain.adapter_id != markdown.adapter_id
    assert plain.skill_bundle_id == markdown.skill_bundle_id == bundle.skill_bundle_id
    assert plain.skill_bundle_hash == markdown.skill_bundle_hash == bundle.digest


def test_third_party_skill_requires_explicit_review_evaluation_and_activation() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    source = SkillSource(
        source_url="https://example.invalid/skill",
        source_revision="abc123",
        license="MIT",
    )
    discovered = service.create_skill(
        replace(
            _profile("External", "external", enabled=False),
            source=source,
            trust_status=SkillTrustStatus.DISCOVERED,
        ),
        owner_ref=OWNER,
    )

    with pytest.raises(ContractError) as activation_error:
        service.set_enabled(discovered.skill_id, True, expected_revision=1)
    assert activation_error.value.code is ErrorCode.FORBIDDEN

    verified = service.transition_trust(
        discovered.skill_id,
        SkillTrustStatus.SOURCE_VERIFIED,
        expected_revision=1,
    )
    reviewed = service.transition_trust(
        discovered.skill_id,
        SkillTrustStatus.SECURITY_REVIEWED,
        expected_revision=verified.revision,
    )
    pilot = service.transition_trust(
        discovered.skill_id,
        SkillTrustStatus.PILOT,
        expected_revision=reviewed.revision,
    )
    adopted = service.transition_trust(
        discovered.skill_id,
        SkillTrustStatus.ADOPTED,
        expected_revision=pilot.revision,
        evaluation_status=SkillEvaluationStatus.PASSED,
        evaluation_metadata={"suite": "issue-588", "passed": True},
    )
    active = service.set_enabled(
        discovered.skill_id,
        True,
        expected_revision=adopted.revision,
    )

    assert active.profile.enabled is True
    assert active.profile.trust_status is SkillTrustStatus.ADOPTED
    assert active.profile.evaluation_metadata["suite"] == "issue-588"


def test_restart_persistence_retains_exact_agent_run_bundle_binding(tmp_path) -> None:
    path = tmp_path / "skills.json"
    repository = JsonSkillRepository(path)
    service = SkillService(repository)
    skill = service.create_skill(_profile("Persisted", "method"), owner_ref=OWNER)
    coordinator = SkillExecutionCoordinator(SkillResolver(repository))
    agent_id = new_id("agent")
    run_id = new_id("run")
    task_id = new_id("task")
    bundle, binding = coordinator.prepare(
        _request(
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            explicit=(skill.ref,),
        )
    )
    record = AgentRunRecord(
        agent_run_id=new_id("agent_run"),
        run_id=run_id,
        task_id=task_id,
        agent=AgentRevisionRef(agent_id=agent_id, revision=1),
        status=AgentRunStatus.RUNNING,
    )
    bound = coordinator.bind_agent_run(binding.binding_id, record)

    restored = JsonSkillRepository(path)
    restored_binding = restored.get_binding(binding.binding_id)
    restored_bundle = restored.get_bundle(bundle.skill_bundle_id)

    assert restored_binding == bound
    assert restored_binding.agent_run_id == record.agent_run_id
    assert restored_bundle.digest == bundle.digest == restored_binding.skill_bundle_hash
    assert restored_bundle.entries[0].ref == skill.ref


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
    resource = serializers.serialize(SKILL_RESOURCE_TYPE, snapshot_skill(source_repository, first.skill_id))
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
