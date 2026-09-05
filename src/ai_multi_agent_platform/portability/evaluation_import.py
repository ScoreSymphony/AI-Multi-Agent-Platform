"""Rollback-capable EvaluationSuite import through the Evaluation owning service."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.evaluation.models import EvaluationSuite
from ai_multi_agent_platform.evaluation.service import EvaluationService, evaluation_suite_ref

from .evaluation_codecs import EVALUATION_SUITE_RESOURCE_TYPE
from .models import PortableResource
from .registry import ImportContext


@dataclass(frozen=True, slots=True)
class EvaluationSuiteImportToken:
    suite_ref: str
    checksum: str


class EvaluationSuiteImportMutationHandler:
    """Create exact Suite versions through Evaluation and compensate safely."""

    resource_type = EVALUATION_SUITE_RESOURCE_TYPE

    def __init__(self, evaluation: EvaluationService) -> None:
        self._evaluation = evaluation

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        suite = _require_suite(value)
        reference = evaluation_suite_ref(suite)
        try:
            self._evaluation.get_suite(reference)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"EvaluationSuite appeared after import preview: {reference}",
            details={"suite_ref": reference},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        suite = _require_suite(value)
        checksum = self._evaluation.create_suite(suite)
        return EvaluationSuiteImportToken(
            suite_ref=evaluation_suite_ref(suite),
            checksum=checksum,
        )

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, EvaluationSuiteImportToken):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable EvaluationSuite rollback token is invalid",
            )
        self._evaluation.delete_suite(
            token.suite_ref,
            expected_checksum=token.checksum,
        )


def _require_suite(value: object) -> EvaluationSuite:
    if not isinstance(value, EvaluationSuite):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable EvaluationSuite mutation handler received the wrong decoded resource type",
        )
    return value


__all__ = [
    "EvaluationSuiteImportMutationHandler",
    "EvaluationSuiteImportToken",
]
