from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationPolicyAssignment,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    JsonAuthorizationPolicyProfileRepository,
    ResourceType,
)


def _profile_values() -> tuple[
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
]:
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="user", id="atomic-owner")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
    )
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=1,
        owner_ref=owner,
        content=AuthorizationPolicyProfileContent(
            name="Atomic persistence fixture",
            allowed_actions=(AuthorizationAction.READ,),
            resource_types=(ResourceType.GENERIC,),
            provenance=AuthorizationPolicyProvenance(
                created_by="user:atomic-owner",
                source="local",
            ),
        ),
        created_at=definition.created_at,
    )
    return definition, revision


def _fail_save() -> None:
    raise OSError("simulated durable write failure")


def test_create_rolls_back_memory_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = JsonAuthorizationPolicyProfileRepository(tmp_path / "policies.json")
    definition, revision = _profile_values()
    monkeypatch.setattr(repository, "_save", _fail_save)

    with pytest.raises(OSError, match="simulated durable write failure"):
        repository.create_profile(definition, revision)

    assert repository.list_profiles() == ()
    assert repository.list_assignments() == ()


def test_append_revision_rolls_back_memory_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "policies.json"
    repository = JsonAuthorizationPolicyProfileRepository(path)
    definition, revision = _profile_values()
    repository.create_profile(definition, revision)
    updated_at = datetime.now(UTC)
    updated = replace(definition, current_revision=2, updated_at=updated_at)
    second = replace(revision, revision=2, created_at=updated_at)
    monkeypatch.setattr(repository, "_save", _fail_save)

    with pytest.raises(OSError, match="simulated durable write failure"):
        repository.append_revision(updated, second)

    assert repository.get_profile(definition.policy_profile_id) == definition
    assert repository.list_revisions(definition.policy_profile_id) == (revision,)
    restored = JsonAuthorizationPolicyProfileRepository(path)
    assert restored.get_profile(definition.policy_profile_id) == definition
    assert restored.list_revisions(definition.policy_profile_id) == (revision,)


def test_lifecycle_change_rolls_back_memory_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "policies.json"
    repository = JsonAuthorizationPolicyProfileRepository(path)
    definition, revision = _profile_values()
    repository.create_profile(definition, revision)
    disabled = replace(definition, enabled=False, updated_at=datetime.now(UTC))
    monkeypatch.setattr(repository, "_save", _fail_save)

    with pytest.raises(OSError, match="simulated durable write failure"):
        repository.set_enabled(disabled)

    assert repository.get_profile(definition.policy_profile_id) == definition
    restored = JsonAuthorizationPolicyProfileRepository(path)
    assert restored.get_profile(definition.policy_profile_id) == definition


def test_assignment_rolls_back_memory_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "policies.json"
    repository = JsonAuthorizationPolicyProfileRepository(path)
    definition, revision = _profile_values()
    repository.create_profile(definition, revision)
    assignment = AuthorizationPolicyAssignment(
        profile_ref=AuthorizationPolicyProfileRef(definition.policy_profile_id, 1),
        principal_ref="user:consumer",
        actor_types=(ActorType.HUMAN,),
        assigned_by="user:atomic-owner",
    )
    monkeypatch.setattr(repository, "_save", _fail_save)

    with pytest.raises(OSError, match="simulated durable write failure"):
        repository.create_assignment(assignment)

    assert repository.list_assignments() == ()
    restored = JsonAuthorizationPolicyProfileRepository(path)
    assert restored.list_assignments() == ()
