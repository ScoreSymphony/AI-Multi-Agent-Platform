from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, FileState, LocalFileProvider
from ai_multi_agent_platform.domain import Artifact, ExternalRef, OwnerRef, new_id
from ai_multi_agent_platform.portability import (
    ARTIFACT_RESOURCE_TYPE,
    FILE_RESOURCE_TYPE,
    ArtifactPortableCodec,
    FilePortableCodec,
    FilePortableSnapshot,
    IdPolicy,
    ImportContext,
    ImportPreviewService,
    PackageProvenance,
    ResourceSerializerRegistry,
    build_package,
    materialize_file,
    register_file_portability_codecs,
    snapshot_file,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-79-file",
            owner_type="user",
            owner_id="portable-owner",
            project_id=project_id,
        ),
        actor_ref="user:portable-owner",
    )


def test_file_artifact_package_round_trip_remaps_provider_and_ids(tmp_path: Path) -> None:
    project_id = new_id("project")
    source_context = _context(project_id)
    source = LocalFileProvider(tmp_path / "source-objects", tmp_path / "source.sqlite")
    artifact = Artifact(
        id=new_id("artifact"),
        name="report.txt",
        owner_ref=OwnerRef(type="user", id="portable-owner"),
        media_type="text/plain",
        uri="file:///source/private/report.txt",
        project_id=project_id,
        external_refs=(ExternalRef(system="example", kind="source", value="report-1"),),
    )
    created = asyncio.run(
        source.create_file(
            b"portable file bytes",
            source_context,
            content_type="text/plain",
            metadata={"purpose": "issue-79"},
        )
    )
    asyncio.run(source.link_artifact(created.file_id, artifact.id, source_context))

    registry = ResourceSerializerRegistry()
    register_file_portability_codecs(
        registry,
        file_id_policy=IdPolicy.REGENERATE,
        artifact_id_policy=IdPolicy.REGENERATE,
    )
    file_resource = registry.serialize(
        FILE_RESOURCE_TYPE,
        asyncio.run(snapshot_file(source, created.file_id, source_context)),
    )
    artifact_resource = registry.serialize(ARTIFACT_RESOURCE_TYPE, artifact)
    package = build_package(
        source_platform_version="0.0.1",
        resources=(file_resource, artifact_resource),
        provenance=PackageProvenance(source="test"),
    )

    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    assert preview.ready is True
    assert preview.import_order == (
        (ARTIFACT_RESOURCE_TYPE, artifact.id),
        (FILE_RESOURCE_TYPE, created.file_id),
    )

    context = ImportContext(id_mapping=preview.mapping_dict())
    imported_artifact = registry.deserialize(artifact_resource, context)
    imported_file = registry.deserialize(file_resource, context)
    assert isinstance(imported_artifact, Artifact)
    assert isinstance(imported_file, FilePortableSnapshot)
    assert imported_artifact.id != artifact.id
    assert imported_artifact.uri is None
    assert imported_artifact.external_refs == artifact.external_refs
    assert imported_file.record.file_id != created.file_id
    assert imported_file.record.artifact_ids == (imported_artifact.id,)
    assert imported_file.data == b"portable file bytes"

    target = LocalFileProvider(tmp_path / "target-objects", tmp_path / "target.sqlite")
    target_record = asyncio.run(materialize_file(imported_file, target, source_context))
    assert target_record.sha256 == imported_file.record.sha256
    assert target_record.artifact_ids == (imported_artifact.id,)
    assert asyncio.run(target.read(target_record.file_id, source_context.operation)) == imported_file.data


def test_file_snapshot_rejects_bytes_that_do_not_match_canonical_checksum(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "objects", tmp_path / "data.sqlite")
    record = asyncio.run(provider.create_file(b"original", context))

    with pytest.raises(ValueError, match="checksum"):
        FilePortableSnapshot(record=record, data=b"tampered")


def test_artifact_codec_never_exports_source_uri() -> None:
    artifact = Artifact(
        id=new_id("artifact"),
        name="private-path.txt",
        owner_ref=OwnerRef(type="user", id="portable-owner"),
        uri="file:///srv/private/private-path.txt",
    )
    codec = ArtifactPortableCodec()

    resource = codec.serialize(artifact)

    assert "file:///srv/private/private-path.txt" not in str(resource.payload)
    assert resource.payload["source_uri_omitted"] is True


def test_file_materialization_rolls_back_when_artifact_link_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    source = LocalFileProvider(tmp_path / "source", tmp_path / "source.sqlite")
    artifact_id = new_id("artifact")
    record = asyncio.run(source.create_file(b"rollback-me", context))
    linked = asyncio.run(source.link_artifact(record.file_id, artifact_id, context))
    snapshot = asyncio.run(snapshot_file(source, linked.file_id, context))

    target = LocalFileProvider(tmp_path / "target", tmp_path / "target.sqlite")

    async def fail_link(
        _file_id: str,
        _artifact_id: str,
        _context: DataAccessContext,
    ) -> object:
        raise ContractError(
            code="backend_error",  # type: ignore[arg-type]
            message="simulated artifact-link failure",
        )

    monkeypatch.setattr(target, "link_artifact", fail_link)

    with pytest.raises(ContractError, match="simulated artifact-link failure"):
        asyncio.run(materialize_file(snapshot, target, context))

    rolled_back = asyncio.run(target.get_file(record.file_id, context))
    assert rolled_back.state is FileState.TOMBSTONED


def test_file_codec_can_be_registered_independently() -> None:
    registry = ResourceSerializerRegistry()
    registry.register(FilePortableCodec())
    registry.register(ArtifactPortableCodec())

    assert registry.resource_types() == (ARTIFACT_RESOURCE_TYPE, FILE_RESOURCE_TYPE)
