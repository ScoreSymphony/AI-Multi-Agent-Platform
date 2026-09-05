"""Rollback-safe #79 import handler for canonical authorization policy profiles."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileRepository,
    AuthorizationPolicyProfileService,
)

from .models import IdPolicy, PortableResource
from .policy_profile_codecs import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableSnapshot,
    register_authorization_policy_profile_portability_codec,
    snapshot_authorization_policy_profile,
)
from .registry import ImportContext, ResourceSerializerRegistry


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileImportToken:
    policy_profile_id: str


class AuthorizationPolicyProfileImportMutationHandler:
    """Import policy history as disabled/untrusted configuration only.

    Materialization is delegated to the canonical policy-profile lifecycle service, so
    #79 cannot create a second authorization or persistence path. Source ownership is
    explicitly replaced with a destination owner; assignments are never transported.
    """

    resource_type = AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE

    def __init__(
        self,
        service: AuthorizationPolicyProfileService,
        *,
        import_context: AuthorizationPolicyProfileCallContext,
        target_owner_ref: OwnerRef,
    ) -> None:
        self._service = service
        self._import_context = import_context
        self._target_owner_ref = target_owner_ref

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        snapshot = self._with_target_owner(_require_snapshot(value))
        expected_target = context.remap(self.resource_type, resource.resource_id)
        if snapshot.definition.policy_profile_id != expected_target:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "decoded policy profile target ID disagrees with import preview mapping",
            )
        if snapshot.definition.enabled:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable policy profile must remain dormant during import",
            )
        if any(
            not revision.content.provenance.imported
            or revision.content.provenance.trusted
            for revision in snapshot.revisions
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable policy profile must be imported as untrusted configuration",
            )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        snapshot = self._with_target_owner(_require_snapshot(value))
        expected_target = context.remap(self.resource_type, resource.resource_id)
        if snapshot.definition.policy_profile_id != expected_target:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile import target changed after preflight",
            )
        imported = await self._service.import_profile(
            definition=snapshot.definition,
            revisions=snapshot.revisions,
            context=self._import_context,
        )
        return AuthorizationPolicyProfileImportToken(imported.policy_profile_id)

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, AuthorizationPolicyProfileImportToken):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "invalid policy profile import rollback token",
            )
        self._service.compensate_import(token.policy_profile_id)

    def _with_target_owner(
        self,
        snapshot: AuthorizationPolicyProfilePortableSnapshot,
    ) -> AuthorizationPolicyProfilePortableSnapshot:
        definition = replace(snapshot.definition, owner_ref=self._target_owner_ref)
        revisions = tuple(
            replace(revision, owner_ref=self._target_owner_ref)
            for revision in snapshot.revisions
        )
        return AuthorizationPolicyProfilePortableSnapshot(definition, revisions)


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
    "AuthorizationPolicyProfileImportToken",
    "load_authorization_policy_profile_snapshot",
    "register_authorization_policy_profile_serializer",
]
