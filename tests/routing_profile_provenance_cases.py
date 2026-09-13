from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRepository,
    ModelRoutingProfileService,
)

OWNER = OwnerRef(type="user", id="user-issue-446")
CONTEXT = OperationContext(
    correlation_id="corr-issue-446",
    owner_type=OWNER.type,
    owner_id=OWNER.id,
)


def _create_two_revision_profile(repository: JsonModelRoutingProfileRepository) -> str:
    service = ModelRoutingProfileService(repository)
    first = asyncio.run(
        service.create_profile(
            name="Immutable provenance",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=CONTEXT,
        )
    )
    asyncio.run(
        service.version_profile(
            first.profile_id,
            name="Immutable provenance v2",
            policy=ModelRoutingProfilePolicy(),
            principal_ref=OWNER.id,
            context=CONTEXT,
            expected_revision=1,
        )
    )
    return first.profile_id


def test_repository_contract_exposes_guarded_compensation_not_general_history_delete() -> None:
    assert hasattr(ModelRoutingProfileRepository, "compensate_profile_creation")
    assert not hasattr(ModelRoutingProfileRepository, "delete_profile")


def test_compensation_refuses_to_delete_history_that_advanced(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    repository = JsonModelRoutingProfileRepository(path)
    profile_id = _create_two_revision_profile(repository)

    with pytest.raises(ContractError) as caught:
        repository.compensate_profile_creation(
            profile_id,
            expected_current_revision=1,
        )

    assert caught.value.code is ErrorCode.CONFLICT
    assert repository.get_definition(profile_id).current_revision == 2
    assert tuple(item.revision for item in repository.list_revisions(profile_id)) == (1, 2)

    restarted = JsonModelRoutingProfileRepository(path)
    assert restarted.get_definition(profile_id).current_revision == 2
    assert tuple(item.revision for item in restarted.list_revisions(profile_id)) == (1, 2)
