from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRevision,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.portability import ModelRoutingProfilePortableSnapshot

_OWNER = OwnerRef(type="user", id="user-issue-444")
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _create_profile(
    path: Path,
    *,
    definition_updated_at: datetime | None = None,
    revision_created_at: datetime | None = None,
) -> tuple[
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileDefinition,
    ModelRoutingProfileRevision,
]:
    profile_id = new_model_routing_profile_id()
    revision_time = revision_created_at or (_BASE + timedelta(seconds=1))
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=_OWNER,
        current_revision=1,
        created_at=_BASE,
        updated_at=definition_updated_at or (_BASE + timedelta(seconds=3)),
    )
    revision = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Initial",
        owner_ref=_OWNER,
        policy=ModelRoutingProfilePolicy(),
        created_at=revision_time,
    )
    repository = JsonModelRoutingProfileRepository(path)
    repository.create_profile(definition, revision)
    return repository, definition, revision


def test_repository_rejects_backdated_revision_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    repository, definition, first = _create_profile(path)
    durable_before = path.read_text(encoding="utf-8")

    second = ModelRoutingProfileRevision(
        profile_id=definition.profile_id,
        revision=2,
        name="Backdated",
        owner_ref=_OWNER,
        created_at=first.created_at - timedelta(seconds=1),
    )
    updated = replace(
        definition,
        current_revision=2,
        updated_at=definition.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(ContractError) as exc_info:
        repository.update_profile(updated, second)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert repository.get_definition(definition.profile_id) == definition
    assert repository.list_revisions(definition.profile_id) == (first,)
    assert path.read_text(encoding="utf-8") == durable_before


def test_repository_rejects_backwards_stable_update_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    repository, definition, first = _create_profile(path)
    durable_before = path.read_text(encoding="utf-8")
    second_time = first.created_at + timedelta(seconds=1)
    second = ModelRoutingProfileRevision(
        profile_id=definition.profile_id,
        revision=2,
        name="Second",
        owner_ref=_OWNER,
        created_at=second_time,
    )
    updated = replace(
        definition,
        current_revision=2,
        updated_at=second_time,
    )
    assert updated.updated_at < definition.updated_at

    with pytest.raises(ContractError) as exc_info:
        repository.update_profile(updated, second)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert repository.get_definition(definition.profile_id) == definition
    assert repository.list_revisions(definition.profile_id) == (first,)
    assert path.read_text(encoding="utf-8") == durable_before


def test_repository_rejects_definition_that_predates_latest_revision(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    profile_id = new_model_routing_profile_id()
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=_OWNER,
        current_revision=1,
        created_at=_BASE,
        updated_at=_BASE + timedelta(seconds=1),
    )
    revision = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Future revision",
        owner_ref=_OWNER,
        created_at=_BASE + timedelta(seconds=2),
    )
    repository = JsonModelRoutingProfileRepository(path)

    with pytest.raises(ContractError) as exc_info:
        repository.create_profile(definition, revision)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert not path.exists()
    assert repository.list_definitions() == ()


def test_restart_rejects_temporally_contradictory_persisted_history(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    repository, definition, first = _create_profile(path)
    second_time = first.created_at + timedelta(seconds=1)
    second = ModelRoutingProfileRevision(
        profile_id=definition.profile_id,
        revision=2,
        name="Second",
        owner_ref=_OWNER,
        created_at=second_time,
    )
    updated = replace(
        definition,
        current_revision=2,
        updated_at=second_time + timedelta(seconds=1),
    )
    repository.update_profile(updated, second)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profiles"][0]["revisions"][1]["created_at"] = (
        first.created_at - timedelta(seconds=1)
    ).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError) as exc_info:
        JsonModelRoutingProfileRepository(path)

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_restart_rejects_updated_at_before_latest_revision(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    repository, definition, first = _create_profile(path)
    second_time = first.created_at + timedelta(seconds=1)
    repository.update_profile(
        replace(
            definition,
            current_revision=2,
            updated_at=second_time + timedelta(seconds=1),
        ),
        ModelRoutingProfileRevision(
            profile_id=definition.profile_id,
            revision=2,
            name="Second",
            owner_ref=_OWNER,
            created_at=second_time,
        ),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profiles"][0]["definition"]["updated_at"] = first.created_at.isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError) as exc_info:
        JsonModelRoutingProfileRepository(path)

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_portable_snapshot_rejects_backwards_revision_chronology() -> None:
    profile_id = new_model_routing_profile_id()
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=_OWNER,
        current_revision=2,
        created_at=_BASE,
        updated_at=_BASE + timedelta(seconds=4),
    )
    first = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="First",
        owner_ref=_OWNER,
        created_at=_BASE + timedelta(seconds=2),
    )
    second = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=2,
        name="Second",
        owner_ref=_OWNER,
        created_at=_BASE + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="chronology"):
        ModelRoutingProfilePortableSnapshot(definition, (first, second))


def test_portable_snapshot_rejects_definition_before_latest_revision() -> None:
    profile_id = new_model_routing_profile_id()
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=_OWNER,
        current_revision=1,
        created_at=_BASE,
        updated_at=_BASE + timedelta(seconds=1),
    )
    revision = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Latest",
        owner_ref=_OWNER,
        created_at=_BASE + timedelta(seconds=2),
    )

    with pytest.raises(ValueError, match="latest revision"):
        ModelRoutingProfilePortableSnapshot(definition, (revision,))


def test_equal_timestamps_remain_valid_for_repository_and_portability(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    profile_id = new_model_routing_profile_id()
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=_OWNER,
        current_revision=1,
        created_at=_BASE,
        updated_at=_BASE,
    )
    first = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="First",
        owner_ref=_OWNER,
        created_at=_BASE,
    )
    repository = JsonModelRoutingProfileRepository(path)
    repository.create_profile(definition, first)

    second = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=2,
        name="Second",
        owner_ref=_OWNER,
        created_at=_BASE,
    )
    updated = replace(definition, current_revision=2, updated_at=_BASE)
    repository.update_profile(updated, second)

    snapshot = ModelRoutingProfilePortableSnapshot(updated, (first, second))
    restarted = JsonModelRoutingProfileRepository(path)

    assert snapshot.revisions == (first, second)
    assert restarted.list_revisions(profile_id) == (first, second)
