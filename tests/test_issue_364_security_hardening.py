from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.workflows import (
    InMemoryWorkflowRepository,
    WorkflowCompatibility,
    WorkflowContent,
    WorkflowService,
    WorkflowStage,
)


@pytest.mark.parametrize(
    "metadata",
    [
        {"provider_id": "provider-a"},
        {"nested": {"orchestrator_id": "orchestrator-a"}},
        {"github_token": "plaintext-token"},
        {"nested": {"client-secret": "plaintext-secret"}},
    ],
)
def test_provider_private_and_secret_bearing_metadata_variants_are_rejected(
    metadata: dict[str, object],
) -> None:
    service = WorkflowService(InMemoryWorkflowRepository())
    content = WorkflowContent(
        name="Unsafe workflow",
        description="",
        stages=(WorkflowStage(stage_id="one", title="One"),),
        metadata=metadata,  # type: ignore[arg-type]
    )

    with pytest.raises(ContractError) as exc_info:
        service.create(owner_ref=OwnerRef(type="user", id="alice"), content=content)

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    ("compatibility", "message"),
    [
        ({"provider_agnostic": False}, "provider agnostic"),
        ({"orchestrator_agnostic": False}, "orchestrator agnostic"),
    ],
)
def test_canonical_workflow_compatibility_cannot_bind_to_one_backend(
    compatibility: dict[str, bool],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        WorkflowCompatibility(**compatibility)  # type: ignore[arg-type]


def test_safe_portability_metadata_and_secret_reference_names_remain_allowed() -> None:
    service = WorkflowService(InMemoryWorkflowRepository())
    content = WorkflowContent(
        name="Portable workflow",
        description="",
        stages=(
            WorkflowStage(
                stage_id="one",
                title="One",
                metadata={"secret_reference_hint": "vault-reference-only"},
            ),
        ),
        metadata={"provider_agnostic_note": "portable", "tokenizer": "generic"},
    )

    revision = service.create(owner_ref=OwnerRef(type="user", id="alice"), content=content)

    assert revision.content.metadata["provider_agnostic_note"] == "portable"
