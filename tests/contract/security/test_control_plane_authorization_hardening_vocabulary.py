"""Migrated under #722; original coverage tracked issue #15."""

# ruff: noqa: F401

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
)
from ai_multi_agent_platform.domain import Approval, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedDataFileProvider,
    AuthorizedDataKnowledgeProvider,
    AuthorizedDataMemoryProvider,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    canonical_control_plane_vocabulary,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _data_context(*, project_id: str | None = None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-data-hardening",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def test_control_plane_vocabulary_mapping_is_platform_owned() -> None:
    assert canonical_control_plane_vocabulary("project:create") == (
        AuthorizationAction.CREATE,
        ResourceType.PROJECT,
    )
    assert canonical_control_plane_vocabulary("model-provider:disable") == (
        AuthorizationAction.ADMINISTER,
        ResourceType.PROVIDER_CONFIGURATION,
    )
