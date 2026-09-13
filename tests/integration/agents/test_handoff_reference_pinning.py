from __future__ import annotations

import asyncio
from typing import cast

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.context import ContextBundleRepository
from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import HandoffSourceKind, HandoffSourceRef
from ai_multi_agent_platform.handoffs.production import (
    CanonicalHandoffReferenceGateway,
    _ResolvedReference,
)
from ai_multi_agent_platform.research import ResearchRepository
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import SkillRepository
from ai_multi_agent_platform.verification import VerificationEvidenceResolver

_CANONICAL_REVISION = "7"
_CANONICAL_DIGEST = "a" * 64


class _PinnedReferenceGateway(CanonicalHandoffReferenceGateway):
    async def _resolve(
        self,
        reference: HandoffSourceRef,
        *,
        task_id: str,
    ) -> _ResolvedReference:
        _ = reference, task_id
        return _ResolvedReference(
            revision=_CANONICAL_REVISION,
            digest=_CANONICAL_DIGEST,
        )


def _gateway() -> _PinnedReferenceGateway:
    return _PinnedReferenceGateway(
        authorization=cast(AuthorizationProvider, object()),
        verification=cast(VerificationEvidenceResolver, object()),
        research=cast(ResearchRepository, object()),
        skills=cast(SkillRepository, object()),
        contexts=cast(ContextBundleRepository, object()),
    )


@pytest.mark.parametrize(
    ("revision", "digest", "expected_fragment"),
    (
        (None, _CANONICAL_DIGEST, "revision"),
        (_CANONICAL_REVISION, None, "digest"),
    ),
)
def test_prepare_read_rejects_unpinned_source_reference(
    revision: str | None,
    digest: str | None,
    expected_fragment: str,
) -> None:
    async def scenario() -> None:
        gateway = _gateway()
        participant = AgentRevisionRef(agent_id=new_id("agent"), revision=1)
        reference = HandoffSourceRef(
            kind=HandoffSourceKind.CONTEXT_BUNDLE,
            resource_id="context_bundle_reference_pinning_651",
            revision=revision,
            digest=digest,
        )
        token = gateway.begin()
        try:
            with pytest.raises(ContractError) as captured:
                await gateway.prepare_read(
                    participant,
                    reference,
                    task_id=new_id("task"),
                    run_id=None,
                    actor=ActorIdentity(participant.agent_id, ActorType.AGENT),
                    operation=OperationContext(correlation_id="handoff-reference-pinning-651"),
                )
        finally:
            gateway.reset(token)

        assert captured.value.code is ErrorCode.NOT_FOUND
        assert expected_fragment in str(captured.value)

    asyncio.run(scenario())
