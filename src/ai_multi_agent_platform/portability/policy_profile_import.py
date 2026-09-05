"""Rollback-safe #79 import handler for canonical authorization policy profiles."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.security.policy_profile_import_service import (
    AuthorizationPolicyProfileImportService,
)
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileRepository,
)

from .models import IdPolicy, PortableResource
from .policy_profile_codecs import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableSnapshot,
    register_authorization_policy_profile_portability_codec,
    snapshot_authorization_policy_profile,
)
from .registry import ImportContext, ResourceSerializerRegistry


class AuthorizationPolicyProfileImportMutationHandler:
    """Import policy history as disabled/untrusted configuration only."""

    resource_type = AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE

    def __init__(
        self,
        service: AuthorizationPolicyProfileImportService,
        repository: AuthorizationPolicyProfileRepository,
        *,
        import_context: AuthorizationPolicyProfileCallContext,
    ) -> None:
        self._service = service
        self._repository = repository
        self._import_context = import_context

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        try:
            self._repository.get_profile(snapshot.definition.policy_profile_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            "authorization policy profile appeared after import preview",
            details={"policy_profile_id": snapshot.definition.policy_profile_id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del context
        snapshot = _require_snapshot(value)
        imported = await self._service.import_history(
            snapshot.definition,
            snapshot.revisions,
            context=self._import_context,
            source_reference=f"portable-resource:{resource.checksum}",
        )
        return imported.policy_profile_id

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
                "policy profile rollback token must be the imported canonical ID",
            )
        self._service.compensate_import(token)


def _require_snapshot(value: object) -> AuthorizationPolicyProfilePortableSnapshot:
    if not isinstance(value, AuthorizationPolicyProfilePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "policy profile import handler received the wrong decoded resource type",
        )
    return value


async def load_authorization_policy_profile_snapshot(
    repository: AuthorizationPolicyProfileRepository,
    policy_profile_id: str,
) -> object:
    """Export-source adapter for the existing #79 ``ExportSourceRegistry``."""

    return snapshot_authorization_policy_profile(repository, policy_profile_id)


def register_authorization_policy_profile_serializer(
    serializers: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    """Small composition helper kept separate from the generic #79 registry core."""

    register_authorization_policy_profile_portability_codec(
        serializers,
        id_policy=id_policy,
    )


__all__ = [
    "AuthorizationPolicyProfileImportMutationHandler",
    "load_authorization_policy_profile_snapshot",
    "register_authorization_policy_profile_serializer",
]
