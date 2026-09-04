"""Privacy-aware KnowledgeSource import and destination index rebuild for issue #79."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.data.contracts import KnowledgeProvider
from ai_multi_agent_platform.data.models import DataAccessContext, KnowledgeStatus

from .knowledge_codecs import KNOWLEDGE_SOURCE_RESOURCE_TYPE, KnowledgePortableSnapshot
from .models import PortableResource
from .registry import ImportContext


@dataclass(frozen=True, slots=True)
class KnowledgeImportPrivacyPolicy:
    """Explicit exception to conservative KnowledgeSource ownership migration."""

    allow_owner_transfer: bool = False


class KnowledgeSourceImportMutationHandler:
    """Register canonical source metadata and rebuild destination-owned index state."""

    resource_type = KNOWLEDGE_SOURCE_RESOURCE_TYPE

    def __init__(
        self,
        provider: KnowledgeProvider,
        data_context: DataAccessContext,
        *,
        privacy_policy: KnowledgeImportPrivacyPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._data_context = data_context
        self._privacy_policy = privacy_policy or KnowledgeImportPrivacyPolicy()

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        _validate_privacy(snapshot, self._data_context, self._privacy_policy)
        try:
            await self._provider.get_index_status(snapshot.source.source_id, self._data_context)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"KnowledgeSource appeared after import preview: {snapshot.source.source_id}",
            details={"source_id": snapshot.source.source_id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_snapshot(value)
        source = replace(
            snapshot.source,
            status=KnowledgeStatus.REGISTERED,
            content_checksum=None,
        )
        await self._provider.register_source(source, self._data_context)
        try:
            document = snapshot.document
            if document is not None:
                imported = await self._provider.ingest_source(
                    source.source_id,
                    document.content,
                    document.location,
                    self._data_context,
                )
                if imported.checksum != document.checksum:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "destination KnowledgeProvider changed canonical source content",
                        details={"source_id": source.source_id},
                    )
                index = await self._provider.get_index_status(source.source_id, self._data_context)
                if index.revision != source.revision or index.status is not KnowledgeStatus.READY:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        (
                            "destination KnowledgeProvider did not rebuild the imported "
                            "source revision"
                        ),
                        details={"source_id": source.source_id},
                    )
            return source.source_id
        except Exception as exc:
            try:
                await self._provider.remove_source(source.source_id, self._data_context)
            except Exception as rollback_exc:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "KnowledgeSource import failed and source compensation was incomplete",
                    details={"source_id": source.source_id},
                ) from rollback_exc
            raise exc

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
                "portable KnowledgeSource rollback token must be the imported source ID",
            )
        await self._provider.remove_source(token, self._data_context)


def _require_snapshot(value: object) -> KnowledgePortableSnapshot:
    if not isinstance(value, KnowledgePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "KnowledgeSource mutation handler received the wrong decoded resource type",
        )
    return value


def _validate_privacy(
    snapshot: KnowledgePortableSnapshot,
    context: DataAccessContext,
    policy: KnowledgeImportPrivacyPolicy,
) -> None:
    source = snapshot.source
    if source.project_id is not None and source.project_id != context.project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "KnowledgeSource target project does not match its remapped project scope",
            details={"source_id": source.source_id},
        )
    if not policy.allow_owner_transfer and source.owner_ref != context.actor_ref:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "portable KnowledgeSource ownership cannot be transferred implicitly",
            details={"source_id": source.source_id},
        )
