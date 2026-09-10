from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    GateEvidence,
    GateStatus,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.application_distribution.control_plane import (
    _default_build_spec_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import new_id


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.tar.gz",
    )


def _published_release() -> ApplicationRelease:
    return ApplicationRelease(
        application_id="example-app",
        display_name="Example App",
        version="1.2.3",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="a" * 64,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        build_specification=BuildSpecification(command=("true",), targets=(_target(),)),
        creator_ref="user:tester",
        status=ReleaseStatus.PUBLISHED,
        publisher_id="reference-downloads",
        release_url="https://downloads.example/app/v1.2.3",
    )


def test_build_command_allows_repeated_arguments() -> None:
    specification = BuildSpecification(
        command=("tool", "--include", "a", "--include", "b"),
        targets=(_target(),),
    )

    assert specification.command.count("--include") == 2


def test_implicit_build_spec_id_is_stable_for_idempotent_replay() -> None:
    first = RequestContext(
        request_id="request-one",
        correlation_id="correlation",
        actor=ActorContext(principal_ref="user:tester"),
        idempotency_key="release-create-42",
    )
    replay = RequestContext(
        request_id="request-two",
        correlation_id="correlation",
        actor=ActorContext(principal_ref="user:tester"),
        idempotency_key="release-create-42",
    )
    other = RequestContext(
        request_id="request-three",
        correlation_id="correlation",
        actor=ActorContext(principal_ref="user:tester"),
        idempotency_key="release-create-43",
    )

    assert _default_build_spec_id(first) == _default_build_spec_id(replay)
    assert _default_build_spec_id(first) != _default_build_spec_id(other)
    assert _default_build_spec_id(first).startswith("build_spec_")


def test_published_release_rejects_delayed_mutation() -> None:
    async def scenario() -> None:
        repository = InMemoryApplicationReleaseRepository()
        release = _published_release()
        await repository.save(release, expected_revision=0)
        service = ApplicationDistributionService(
            repository,
            kernel=object(),  # type: ignore[arg-type]
            files=object(),  # type: ignore[arg-type]
        )

        with pytest.raises(ContractError) as raised:
            await service.record_gate(
                release.release_id,
                GateEvidence(name="late-gate", status=GateStatus.PASSED),
            )

        assert raised.value.code is ErrorCode.CONFLICT
        persisted = await repository.get(release.release_id)
        assert persisted.status is ReleaseStatus.PUBLISHED
        assert persisted.revision == release.revision
        assert persisted.gates == release.gates

    asyncio.run(scenario())


def test_manifest_schema_uses_platform_neutral_identifier() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "docs"
        / "schemas"
        / "application-release-manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$id"] == "urn:ai-multi-agent-platform:schema:application-release-manifest:v1"
