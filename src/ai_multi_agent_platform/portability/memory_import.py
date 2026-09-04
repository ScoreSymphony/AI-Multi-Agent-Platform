"""Privacy-aware scoped-memory import mutation handler for issue #79."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.data.contracts import MemoryProvider
from ai_multi_agent_platform.data.models import DataAccessContext, MemoryScope

from .memory_codecs import MEMORY_RESOURCE_TYPE, MemoryPortableSnapshot
from .models import PortableResource
from .registry import ImportContext


@dataclass(frozen=True, slots=True)
class MemoryImportPrivacyPolicy:
    """Explicit exceptions to conservative owner/project portability defaults."""

    allow_owner_transfer: bool = False
    allow_explicit_cross_project: bool = False


class MemoryImportMutationHandler:
    resource_type = MEMORY_RESOURCE_TYPE

    def __init__(
        self,
        provider: MemoryProvider,
        data_context: DataAccessContext,
        *,
        privacy_policy: MemoryImportPrivacyPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._data_context = data_context
        self._privacy_policy = privacy_policy or MemoryImportPrivacyPolicy()

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource
        snapshot = _require_memory_snapshot(value)
        _validate_memory_privacy(
            snapshot,
            self._data_context,
            context,
            self._privacy_policy,
        )
        try:
            await self._provider.get_entry(snapshot.entry.memory_id, self._data_context)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Memory appeared after import preview: {snapshot.entry.memory_id}",
            details={"memory_id": snapshot.entry.memory_id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_memory_snapshot(value)
        stored = await self._provider.write_entry(snapshot.entry, self._data_context)
        return stored.memory_id

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
                "portable Memory rollback token must be the imported Memory ID",
            )
        await self._provider.delete_entry(token, self._data_context)


def _require_memory_snapshot(value: object) -> MemoryPortableSnapshot:
    if not isinstance(value, MemoryPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Memory mutation handler received the wrong decoded resource type",
        )
    return value


def _validate_memory_privacy(
    snapshot: MemoryPortableSnapshot,
    data_context: DataAccessContext,
    import_context: ImportContext,
    policy: MemoryImportPrivacyPolicy,
) -> None:
    entry = snapshot.entry
    if entry.scope is MemoryScope.SHORT_TERM:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "short-term execution memory cannot be imported portably",
        )

    if not policy.allow_owner_transfer and entry.owner_ref != data_context.actor_ref:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "portable Memory ownership cannot be transferred implicitly",
            details={"memory_id": entry.memory_id},
        )

    if entry.scope is MemoryScope.USER:
        owner_type = data_context.operation.owner_type
        owner_id = data_context.operation.owner_id
        if owner_type == "user" and owner_id != entry.scope_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "user-scoped Memory cannot be imported into another user scope",
                details={"memory_id": entry.memory_id},
            )

    if entry.scope is MemoryScope.WORKSPACE and entry.scope_id != data_context.project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "workspace Memory target project does not match its remapped scope",
            details={"memory_id": entry.memory_id},
        )

    source_project_id = snapshot.source_project_id
    if source_project_id is None:
        return
    expected_target_project = import_context.remap("project", source_project_id)
    actual_target_project = data_context.project_id
    if expected_target_project == actual_target_project:
        return

    cross_project_rule = entry.access_policy.cross_project_access
    if cross_project_rule in {"deny", "deny_by_default"}:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "Memory scope forbids cross-project portable import",
            details={"memory_id": entry.memory_id, "scope": entry.scope.value},
        )
    if cross_project_rule == "explicit_policy_only" and not policy.allow_explicit_cross_project:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "Memory cross-project import requires an explicit privacy policy grant",
            details={"memory_id": entry.memory_id, "scope": entry.scope.value},
        )
