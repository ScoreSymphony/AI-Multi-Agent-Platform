from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.governance import (
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    SqliteGovernanceRepository,
    TaskConversion,
)

_OWNER = OwnerRef(type="user", id="governance-atomicity")
_REQUESTER = "user:governance-atomicity"


def _proposal() -> Proposal:
    return Proposal(
        title="Atomic governance conversion",
        summary="Exercise stale Specification conversion reservation.",
        reason="Revision validation and reservation must share one SQLite transaction.",
        owner_ref=_OWNER,
        requester_ref=_REQUESTER,
        source="governance-conversion-atomicity",
        status=ProposalStatus.PROPOSED,
    )


def _specification(proposal: Proposal) -> SpecificationRevision:
    return SpecificationRevision(
        proposal_id=proposal.id,
        problem="A stale Specification must never reserve a Task conversion.",
        goal="Keep Specification validation and conversion reservation atomic.",
        scope=("governance conversion reservation",),
        acceptance_criteria=("stale reservations are rejected",),
        owner_ref=_OWNER,
        requester_ref=_REQUESTER,
    )


def test_stale_conversion_is_rejected_across_sqlite_repository_instances(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "governance.sqlite3"
    repository_a = SqliteGovernanceRepository(database_path)
    repository_b = SqliteGovernanceRepository(database_path)

    proposal = repository_a.create_proposal(_proposal())
    revision_one = repository_a.create_specification(_specification(proposal))
    stale_conversion = TaskConversion(
        specification_id=revision_one.id,
        specification_revision=revision_one.revision,
        specification_digest=revision_one.content_digest,
        proposal_id=proposal.id,
        task_id=new_id("task"),
    )

    revision_two = replace(
        revision_one,
        revision=2,
        goal="Reject every conversion prepared from an older Specification revision.",
        content_digest="",
    )
    repository_b.revise_specification(revision_two, expected_revision=1)

    with pytest.raises(ContractError) as error:
        repository_a.reserve_conversion(stale_conversion)

    assert error.value.code is ErrorCode.CONFLICT
    assert repository_a.get_conversion(revision_one.id) is None
