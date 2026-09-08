"""Composition helper for adding canonical EgressProfile portability to #79 workflows."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import EgressProfileRepository

from .egress_profile_codecs import (
    EGRESS_PROFILE_RESOURCE_TYPE,
    register_egress_profile_portability_codec,
    snapshot_egress_profile,
)
from .egress_profile_import import EgressProfileImportMutationHandler
from .executor import ImportMutationRegistry
from .models import IdPolicy
from .registry import ResourceSerializerRegistry
from .workflow import ExportSourceRegistry


@dataclass(frozen=True, slots=True)
class EgressProfilePortabilityBinding:
    """Prepared #79 registration state for durable egress-policy configuration."""

    repository: EgressProfileRepository
    target_owner_ref: OwnerRef
    id_policy: IdPolicy = IdPolicy.PRESERVE

    def register(
        self,
        *,
        serializers: ResourceSerializerRegistry,
        export_sources: ExportSourceRegistry,
        mutations: ImportMutationRegistry,
    ) -> None:
        register_egress_profile_portability_codec(serializers, id_policy=self.id_policy)

        async def load_profile(resource_id: str) -> object:
            return snapshot_egress_profile(self.repository, resource_id)

        export_sources.register(EGRESS_PROFILE_RESOURCE_TYPE, load_profile)
        mutations.register(
            EgressProfileImportMutationHandler(
                self.repository,
                target_owner_ref=self.target_owner_ref,
            )
        )


def register_egress_profile_portability(
    *,
    serializers: ResourceSerializerRegistry,
    export_sources: ExportSourceRegistry,
    mutations: ImportMutationRegistry,
    repository: EgressProfileRepository,
    target_owner_ref: OwnerRef,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    """Register codec, export source and sanitized mutation handler
    as one atomic composition step.
    """

    EgressProfilePortabilityBinding(
        repository=repository,
        target_owner_ref=target_owner_ref,
        id_policy=id_policy,
    ).register(
        serializers=serializers,
        export_sources=export_sources,
        mutations=mutations,
    )


__all__ = [
    "EgressProfilePortabilityBinding",
    "register_egress_profile_portability",
]
