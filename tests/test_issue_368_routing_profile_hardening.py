from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileService,
)

OWNER = OwnerRef(type="user", id="user-routing-owner")


def _context(*, project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-368-hardening",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _raise_persist_failure() -> None:
    raise OSError("simulated durable write failure")


def test_create_rolls_back_in_memory_state_when_persist_fails(tmp_path, monkeypatch) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    monkeypatch.setattr(repository, "_persist", _raise_persist_failure)

    with pytest.raises(OSError, match="simulated durable write failure"):
        asyncio.run(
            service.create_profile(
                name="Create rollback",
                policy=ModelRoutingProfilePolicy(),
                owner_ref=OWNER,
                principal_ref=OWNER.id,
                context=_context(),
            )
        )

    assert repository.list_definitions() == ()


def test_version_rolls_back_definition_and_new_revision_when_persist_fails(
    tmp_path, monkeypatch
) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    first = asyncio.run(
        service.create_profile(
            name="Stable",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(),
        )
    )
    monkeypatch.setattr(repository, "_persist", _raise_persist_failure)

    with pytest.raises(OSError, match="simulated durable write failure"):
        asyncio.run(
            service.version_profile(
                first.profile_id,
                name="Failed update",
                policy=ModelRoutingProfilePolicy(),
                principal_ref=OWNER.id,
                context=_context(),
                expected_revision=1,
            )
        )

    assert repository.get_definition(first.profile_id).current_revision == 1
    assert repository.list_revisions(first.profile_id) == (first,)


def test_enable_state_rolls_back_when_persist_fails(tmp_path, monkeypatch) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    profile = asyncio.run(
        service.create_profile(
            name="Enabled",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(),
        )
    )
    monkeypatch.setattr(repository, "_persist", _raise_persist_failure)

    with pytest.raises(OSError, match="simulated durable write failure"):
        asyncio.run(
            service.set_enabled(
                profile.profile_id,
                False,
                principal_ref=OWNER.id,
                context=_context(),
            )
        )

    assert repository.get_definition(profile.profile_id).enabled is True


def test_delete_rolls_back_complete_history_when_persist_fails(tmp_path, monkeypatch) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    first = asyncio.run(
        service.create_profile(
            name="Delete rollback",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(),
        )
    )
    second = asyncio.run(
        service.version_profile(
            first.profile_id,
            name="Delete rollback v2",
            policy=ModelRoutingProfilePolicy(),
            principal_ref=OWNER.id,
            context=_context(),
            expected_revision=1,
        )
    )
    monkeypatch.setattr(repository, "_persist", _raise_persist_failure)

    with pytest.raises(OSError, match="simulated durable write failure"):
        repository.delete_profile(first.profile_id)

    assert repository.get_definition(first.profile_id).current_revision == 2
    assert repository.list_revisions(first.profile_id) == (first, second)


def test_project_listing_includes_global_profiles_but_not_other_projects(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    service = ModelRoutingProfileService(repository)
    project_a = new_id("project")
    project_b = new_id("project")

    global_profile = asyncio.run(
        service.create_profile(
            name="Global",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(project_id=project_a),
        )
    )
    project_a_profile = asyncio.run(
        service.create_profile(
            name="Project A",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(project_id=project_a),
            project_id=project_a,
        )
    )
    asyncio.run(
        service.create_profile(
            name="Project B",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_context(project_id=project_b),
            project_id=project_b,
        )
    )

    project_a_visible = asyncio.run(
        service.list_profiles(
            principal_ref=OWNER.id,
            context=_context(project_id=project_a),
        )
    )
    global_visible = asyncio.run(
        service.list_profiles(
            principal_ref=OWNER.id,
            context=_context(),
        )
    )

    assert {item.profile_id for item in project_a_visible} == {
        global_profile.profile_id,
        project_a_profile.profile_id,
    }
    assert tuple(item.profile_id for item in global_visible) == (global_profile.profile_id,)
