"""Resolve declarative Application resource/file associations without cross-domain ownership."""

from __future__ import annotations

from dataclasses import dataclass

from .definition import Application
from .models import (
    ApplicationInstance,
    ApplicationObservedState,
    ApplicationResourceAssociation,
)
from .repository import ApplicationRepository


@dataclass(frozen=True, slots=True)
class ApplicationResourceResolution:
    """One installed Application definition matching a canonical resource selector."""

    application: Application
    associations: tuple[ApplicationResourceAssociation, ...]
    instances: tuple[ApplicationInstance, ...]

    @property
    def application_ref(self) -> str:
        return f"{self.application.application_id}@{self.application.version}"

    @property
    def openable_instances(self) -> tuple[ApplicationInstance, ...]:
        """Return non-removed instances that currently expose the declared UI endpoint."""

        manifest = self.application.manifest
        return tuple(
            instance
            for instance in self.instances
            if instance.observed_state is not ApplicationObservedState.REMOVED
            and instance.open_endpoint(manifest) is not None
        )


class ApplicationResourceAssociationResolver:
    """Match installed Application definitions by canonical media/resource type metadata.

    The resolver deliberately accepts metadata only. It does not read a File/Artifact,
    grant access, mount storage, start an Application, or bypass Control Plane policy.
    Consumers remain responsible for binding an approved canonical resource through the
    ordinary Application install/runtime contracts after selecting a compatible handler.
    """

    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        media_type: str | None = None,
        resource_type: str | None = None,
    ) -> tuple[ApplicationResourceResolution, ...]:
        normalized_media_type = _normalize_media_type(media_type)
        normalized_resource_type = _normalize_resource_type(resource_type)
        if normalized_media_type is None and normalized_resource_type is None:
            raise ValueError("media_type or resource_type is required")

        resolutions: list[ApplicationResourceResolution] = []
        for application in self._repository.list_applications():
            associations = tuple(
                association
                for association in application.manifest.resource_associations
                if _association_matches(
                    association,
                    media_type=normalized_media_type,
                    resource_type=normalized_resource_type,
                )
            )
            if not associations:
                continue
            instances = tuple(
                instance
                for instance in self._repository.list_instances(
                    application_id=application.application_id
                )
                if instance.application_version == application.version
            )
            resolutions.append(
                ApplicationResourceResolution(
                    application=application,
                    associations=associations,
                    instances=instances,
                )
            )
        return tuple(resolutions)


def _association_matches(
    association: ApplicationResourceAssociation,
    *,
    media_type: str | None,
    resource_type: str | None,
) -> bool:
    if media_type is not None:
        declared_media_types = {_normalize_media_type(value) for value in association.media_types}
        if media_type not in declared_media_types:
            return False
    if resource_type is not None:
        declared_resource_types = {
            _normalize_resource_type(value) for value in association.resource_types
        }
        if resource_type not in declared_resource_types:
            return False
    return True


def _normalize_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.split(";", maxsplit=1)[0].strip().casefold()
    if not normalized or "/" not in normalized:
        raise ValueError("media_type must be a non-blank MIME type")
    return normalized


def _normalize_resource_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("resource_type must not be blank")
    return normalized


__all__ = [
    "ApplicationResourceAssociationResolver",
    "ApplicationResourceResolution",
]
