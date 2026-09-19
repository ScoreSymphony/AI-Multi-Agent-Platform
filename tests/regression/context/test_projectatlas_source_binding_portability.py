from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories.intelligence.models import (
    RepositoryIntelligenceFreshness,
)
from ai_multi_agent_platform.repositories.intelligence.projectatlas import (
    PROJECTATLAS_ARCHIVE_SHA256,
    PROJECTATLAS_RUNTIME_VERSION,
)
from ai_multi_agent_platform.repositories.intelligence.projectatlas_adapter import (
    ProjectAtlasContainmentEvidence,
    ProjectAtlasSourceBinding,
)

_REVISION = "a" * 40


def test_projectatlas_source_binding_and_containment_use_platform_absolute_paths(
    tmp_path: Path,
) -> None:
    source_root = (tmp_path / "source").resolve()
    database_path = (tmp_path / "state" / "projectatlas.sqlite3").resolve()
    binding = ProjectAtlasSourceBinding(
        repository_id=new_id("external_resource"),
        requested_revision="HEAD",
        resolved_revision=_REVISION,
        source_root=source_root,
        database_path=database_path,
        freshness=RepositoryIntelligenceFreshness.LIVE_WORKSPACE,
        workspace_id=new_id("workspace"),
        workspace_snapshot_id="snapshot-portable",
        materialization_id="materialization-portable",
        source_content_checksum="sha256:workspace-portable",
        dirty=True,
    )

    provenance = binding.provenance_details()
    assert binding.source_root.is_absolute()
    assert binding.database_path.is_absolute()
    assert source_root not in database_path.parents
    assert provenance["resolved_revision"] == _REVISION
    assert provenance["freshness"] == "live_workspace"
    assert provenance["dirty"] is True

    incomplete = ProjectAtlasContainmentEvidence(
        runtime_version=PROJECTATLAS_RUNTIME_VERSION,
        artifact_sha256=PROJECTATLAS_ARCHIVE_SHA256,
        source_read_only=True,
        provider_state_outside_source=True,
        secrets_stripped=True,
        network_egress_denied=False,
        no_new_privileges=True,
        process_boundary="portable-test-boundary",
        platform="test-platform",
        evidence_ref="portable-pending-validation",
    )
    assert incomplete.source_operations_ready is False
    with pytest.raises(ContractError) as blocked:
        incomplete.require_source_operations_ready()
    assert blocked.value.code is ErrorCode.UNAVAILABLE

    ready = ProjectAtlasContainmentEvidence(
        runtime_version=PROJECTATLAS_RUNTIME_VERSION,
        artifact_sha256=PROJECTATLAS_ARCHIVE_SHA256,
        source_read_only=True,
        provider_state_outside_source=True,
        secrets_stripped=True,
        network_egress_denied=True,
        no_new_privileges=True,
        process_boundary="portable-test-boundary",
        platform="test-platform",
        evidence_ref="portable-aggregate-validation",
    )
    assert ready.source_operations_ready is True
