"""Rollback-safe Automation import for issue #79."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.automation.models import IdentityContext
from ai_multi_agent_platform.automation.repository import AutomationRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .automation_codecs import AUTOMATION_RESOURCE_TYPE, AutomationPortableSnapshot
from .models import PortableResource
from .registry import ImportContext


@dataclass(frozen=True, slots=True)
class AutomationImportPolicy:
    """Explicit exception to conservative Automation identity portability defaults."""

    allow_identity_transfer: bool = False


class AutomationImportMutationHandler:
    resource_type = AUTOMATION_RESOURCE_TYPE

    def __init__(
        self,
        repository: AutomationRepository,
        destination_identity: IdentityContext,
        *,
        policy: AutomationImportPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._destination_identity = destination_identity
        self._policy = policy or AutomationImportPolicy()

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        if (
            not self._policy.allow_identity_transfer
            and snapshot.automation.identity != self._destination_identity
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "portable Automation identity cannot be transferred implicitly",
                details={"automation_id": snapshot.automation.id},
            )
        try:
            await self._repository.get_automation(snapshot.automation.id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Automation appeared after import preview: {snapshot.automation.id}",
            details={"automation_id": snapshot.automation.id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_snapshot(value)
        stored = await self._repository.save_automation(snapshot.automation)
        return stored.id

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Automation rollback token must be the imported Automation ID",
            )
        await self._repository.remove_automation_if_unused(token)


def _require_snapshot(value: object) -> AutomationPortableSnapshot:
    if not isinstance(value, AutomationPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Automation mutation handler received the wrong decoded resource type",
        )
    return value
