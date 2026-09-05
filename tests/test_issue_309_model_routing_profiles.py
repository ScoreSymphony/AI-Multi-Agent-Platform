from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    JsonModelRoutingProfileRepository,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    ModelRoutingProfileService,
    RoutingProfileFallbackPolicy,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider, FakeModelProvider


OWNER = OwnerRef(type="user", id="user-routing-owner")


def _context(*, project_id: str | None = None, owner: OwnerRef = OWNER) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-309",
        owner_type=owner.type,
        owner_id=owner.id,
        project_id=project_id,
    )


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(FakeModelProvider())
    registry.register_model(
        ModelConfiguration(
            config_id="model-local-small",
            display_name="Local Small",
            provider_id="fake-model",
            capabilities=ModelCapabilities(context_window=4_096, tool_calling=True),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )
    registry.register_model(
        ModelConfiguration(
            config_id="model-local-large",
            display_name="Local Large",
            provider_id="fake-model",
            capabilities=ModelCapabilities(
                context_window=32_768,
                tool_calling=True,
                structured_output=True,
                streaming=True,
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=10,
        )
    )
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote-large",
            display_name="Remote Large",
            provider_id="fake-model",
            capabilities=ModelCapabilities(
                context_window=64_000,
                tool_calling=True,
                structured_output=True,
                streaming=True,
            ),
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
            priority=200,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_create_version_and_restart_preserve_exact_revisions(tmp_path) -> None:
    path = tmp_path / "routing-profiles.json"
    project_id = new_id("project")
    repository = JsonModelRoutingProfileRepository(path)
    service = ModelRoutingProfileService(repository)

    first = await service.create_profile(
        name="Research",
        description="Prefer the local large-context model.",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(min_context_window=8_000, tool_calling=True),
            preferred_model_ids=("model-local-large",),
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(project_id=project_id),
        project_id=project_id,
        provenance=Provenance(source="issue-309-test", actor_ref=OWNER.id),
    )
    second = await service.version_profile(
        first.profile_id,
        name="Research",
        description="Require structured output as well.",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(
                min_context_window=8_000,
                tool_calling=True,
                structured_output=True,
            ),
            preferred_model_ids=("model-local-large",),
        ),
        expected_revision=1,
        principal_ref=OWNER.id,
        context=_context(project_id=project_id),
        provenance=Provenance(source="issue-309-test", actor_ref=OWNER.id),
    )

    assert first.ref.canonical_ref.endswith("@r1")
    assert second.ref == ModelRoutingProfileRef(first.profile_id, 2)

    restarted = JsonModelRoutingProfileRepository(path)
    assert restarted.get_definition(first.profile_id).current_revision == 2
    assert restarted.get_revision(first.ref).policy.requirements.structured_output is False
    assert restarted.get_revision(second.ref).policy.requirements.structured_output is True


@pytest.mark.asyncio
async def test_exact_profile_revision_drives_deterministic_preference_and_fallback(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    router = DeterministicModelRouter(_registry())

    first = await service.create_profile(
        name="Fallback",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(min_context_window=8_000),
            preferred_model_ids=("model-local-small",),
            fallback=RoutingProfileFallbackPolicy.ROUTE,
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(),
    )
    second = await service.version_profile(
        first.profile_id,
        name="Strict",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(min_context_window=8_000),
            preferred_model_ids=("model-local-small",),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        expected_revision=1,
        principal_ref=OWNER.id,
        context=_context(),
    )

    route = router.route_profile(repository.get_revision(first.ref))
    assert route.model_config_id == "model-remote-large"
    assert first.ref.canonical_ref in route.reason

    with pytest.raises(ContractError) as exc_info:
        router.route_profile(repository.get_revision(second.ref))
    assert exc_info.value.code is ErrorCode.NO_COMPATIBLE_ROUTE
    assert exc_info.value.details["routing_profile_ref"] == second.ref.canonical_ref


@pytest.mark.asyncio
async def test_local_policy_and_ordered_preferences_are_profile_owned(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    profile = await service.create_profile(
        name="Local structured",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(local_only=True, structured_output=True),
            preferred_model_ids=("model-remote-large", "model-local-large"),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(),
    )

    route = DeterministicModelRouter(_registry()).route_profile(profile)
    assert route.model_config_id == "model-local-large"
    assert "ordered canonical preference" in route.reason


@pytest.mark.asyncio
async def test_provider_replacement_does_not_rewrite_profile_identity(tmp_path) -> None:
    registry = _registry()
    router = DeterministicModelRouter(registry)
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    profile = await service.create_profile(
        name="Pinned canonical model",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(explicit_model_id="model-local-large")
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(),
    )

    before = router.route_profile(profile)
    registry.replace_provider(FakeModelProvider(response_text="replacement"))
    after = router.route_profile(repository.get_revision(profile.ref))

    assert before.model_config_id == after.model_config_id == "model-local-large"
    assert repository.get_revision(profile.ref).ref == profile.ref


@pytest.mark.asyncio
async def test_authorization_and_project_scope_are_enforced(tmp_path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    denied = FakeAuthorizationProvider(allowed=False)
    denied_service = ModelRoutingProfileService(
        JsonModelRoutingProfileRepository(tmp_path / "denied.json"),
        authorization=denied,
    )

    with pytest.raises(ContractError) as exc_info:
        await denied_service.create_profile(
            name="Denied",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(project_id=project_a),
            project_id=project_a,
        )
    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert denied.calls[-1].action == "model-routing-profile:create"

    allowed = FakeAuthorizationProvider(allowed=True)
    repository = JsonModelRoutingProfileRepository(tmp_path / "allowed.json")
    service = ModelRoutingProfileService(repository, authorization=allowed)
    profile = await service.create_profile(
        name="Scoped",
        policy=ModelRoutingProfilePolicy(),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(project_id=project_a),
        project_id=project_a,
    )

    with pytest.raises(ContractError) as scope_error:
        await service.get_revision(
            profile.ref,
            principal_ref=OWNER.id,
            context=_context(project_id=project_b),
        )
    assert scope_error.value.code is ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_disable_blocks_execution_resolution_when_requested(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    profile = await service.create_profile(
        name="Disable me",
        policy=ModelRoutingProfilePolicy(),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_context(),
    )
    await service.set_enabled(
        profile.profile_id,
        False,
        principal_ref=OWNER.id,
        context=_context(),
    )

    with pytest.raises(ContractError) as exc_info:
        await service.get_revision(
            profile.ref,
            principal_ref=OWNER.id,
            context=_context(),
            require_enabled=True,
        )
    assert exc_info.value.code is ErrorCode.UNAVAILABLE


def test_persisted_profile_contains_no_provider_private_runtime_state(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    repository = JsonModelRoutingProfileRepository(path)
    profile_id = new_id("model_routing_profile")
    now = datetime.now(UTC)
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=OWNER,
        current_revision=1,
        created_at=now,
        updated_at=now,
    )
    revision = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Portable",
        owner_ref=OWNER,
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(tool_calling=True),
            preferred_model_ids=("canonical-model-config",),
        ),
        created_at=now,
    )
    repository.create_profile(definition, revision)

    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, sort_keys=True)
    for forbidden in (
        '"provider_id"',
        '"provider_native_model_id"',
        '"endpoint"',
        '"node_ref"',
        '"health"',
        '"gateway_state"',
    ):
        assert forbidden not in encoded
