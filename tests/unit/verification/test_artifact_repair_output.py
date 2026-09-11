from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.verification.agent_repair import _resolve_repair_output


def test_result_repair_requires_real_result_id() -> None:
    assert _resolve_repair_output(
        {"result_id": "result_123"},
        requested_subject_type="result",
        run_id="run_123",
    ) == ("result", "result_123")

    with pytest.raises(ContractError) as caught:
        _resolve_repair_output(
            {"artifact_refs": ["artifact_123"]},
            requested_subject_type="result",
            run_id="run_123",
        )

    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION


def test_artifact_repair_accepts_one_canonical_artifact_id() -> None:
    assert _resolve_repair_output(
        {"artifact_id": "artifact_123"},
        requested_subject_type="artifact",
        run_id="run_123",
    ) == ("artifact", "artifact_123")


def test_artifact_repair_accepts_one_capability_artifact_ref() -> None:
    assert _resolve_repair_output(
        {"result_id": "result_ignored", "artifact_refs": ["artifact_123"]},
        requested_subject_type="artifact",
        run_id="run_123",
    ) == ("artifact", "artifact_123")


def test_artifact_repair_fails_closed_for_missing_or_ambiguous_output() -> None:
    for output in (
        {"result_id": "result_123"},
        {"artifact_refs": []},
        {"artifact_refs": ["artifact_1", "artifact_2"]},
        {"artifact_id": "artifact_1", "artifact_refs": ["artifact_2"]},
    ):
        with pytest.raises(ContractError) as caught:
            _resolve_repair_output(
                output,
                requested_subject_type="artifact",
                run_id="run_123",
            )
        assert caught.value.code is ErrorCode.CONTRACT_VIOLATION


def test_artifact_repair_rejects_malformed_artifact_refs() -> None:
    with pytest.raises(ContractError) as caught:
        _resolve_repair_output(
            {"artifact_refs": ["artifact_1", 2]},
            requested_subject_type="artifact",
            run_id="run_123",
        )

    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION


def test_repair_rejects_unknown_subject_kind() -> None:
    with pytest.raises(ContractError) as caught:
        _resolve_repair_output(
            {"result_id": "result_123"},
            requested_subject_type="file",
            run_id="run_123",
        )

    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
