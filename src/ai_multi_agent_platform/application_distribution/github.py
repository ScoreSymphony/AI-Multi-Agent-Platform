"""GitHub Releases publication adapter built on the canonical Connector service."""

from __future__ import annotations

from ai_multi_agent_platform.connectors import ConnectorService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .contracts import PublicationResult, PublishedArtifact, PublishContext
from .models import ApplicationRelease, ReleaseVisibility

_CREATE_RELEASE_ACTION = "github.release.create"
_ATTACH_ASSET_ACTION = "github.release.asset.attach"


class GitHubReleasePublisher:
    """GitHub-specific adapter; canonical release logic never depends on GitHub payloads."""

    provider_id = "github-releases"

    def __init__(self, connectors: ConnectorService, *, connection_id: str, repository_ref: str) -> None:
        self._connectors = connectors
        self._connection_id = connection_id
        self._repository_ref = repository_ref

    async def preview(
        self,
        release: ApplicationRelease,
        manifest: dict[str, JsonValue],
        context: PublishContext,
    ) -> dict[str, JsonValue]:
        del context
        return {
            "provider": self.provider_id,
            "connection_id": self._connection_id,
            "repository_ref": self._repository_ref,
            "tag": f"v{release.version}",
            "visibility": release.visibility.value,
            "artifact_count": len(release.artifacts),
            "manifest": manifest,
            "side_effects": ["create_or_resolve_tag", "create_release", "attach_assets"],
        }

    async def publish(
        self,
        release: ApplicationRelease,
        manifest: dict[str, JsonValue],
        context: PublishContext,
    ) -> PublicationResult:
        create = await self._connectors.invoke_action(
            self._connection_id,
            _CREATE_RELEASE_ACTION,
            {
                "repository_ref": self._repository_ref,
                "tag": f"v{release.version}",
                "version": release.version,
                "channel": release.channel.value,
                "visibility": release.visibility.value,
                "source_revision": release.source_revision,
                "release_notes": release.release_notes,
                "manifest": manifest,
                "fail_if_tag_points_elsewhere": True,
                "fail_if_release_exists_with_different_source": True,
            },
            invocation_id=f"application-release:{release.release_id}:create",
            actor=context.actor,
            context=context.operation,
            approval_id=context.approval_id,
        )
        create_output = _object(create.output, "GitHub release creation")
        release_url = _string(create_output, "release_url")
        external_release_id = _string(create_output, "external_release_id")
        latest_url = _optional_string(create_output, "latest_url")
        published_assets: list[PublishedArtifact] = []
        for artifact in release.artifacts:
            attach = await self._connectors.invoke_action(
                self._connection_id,
                _ATTACH_ASSET_ACTION,
                {
                    "repository_ref": self._repository_ref,
                    "external_release_id": external_release_id,
                    "artifact_id": artifact.artifact_id,
                    "file_id": artifact.file_id,
                    "filename": artifact.filename,
                    "sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                    "fail_if_asset_differs": True,
                },
                invocation_id=f"application-release:{release.release_id}:asset:{artifact.artifact_id}",
                actor=context.actor,
                context=context.operation,
                approval_id=context.approval_id,
            )
            output = _object(attach.output, "GitHub release asset attachment")
            published_assets.append(
                PublishedArtifact(
                    artifact_id=artifact.artifact_id,
                    download_url=_string(output, "download_url"),
                    external_metadata={
                        "github": {
                            "asset_id": _string(output, "external_asset_id"),
                        }
                    },
                )
            )
        visibility = ReleaseVisibility(_string(create_output, "visibility"))
        return PublicationResult(
            provider_id=self.provider_id,
            release_url=release_url,
            latest_url=latest_url,
            visibility=visibility,
            artifacts=tuple(published_assets),
            external_metadata={"github": {"release_id": external_release_id}},
        )


def _object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"{label} returned no object")
    return value


def _string(value: dict[str, JsonValue], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"GitHub release response is missing {field}",
        )
    return item


def _optional_string(value: dict[str, JsonValue], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"GitHub release response has invalid {field}",
        )
    return item
