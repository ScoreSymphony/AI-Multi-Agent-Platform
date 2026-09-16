"""Persistence ports for canonical Application definitions and instances."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .definition import Application
from .models import ApplicationInstance


class ApplicationRepository(ABC):
    """Durable source-of-truth boundary for Application definitions and instances."""

    @abstractmethod
    def save_application(self, application: Application) -> Application: ...

    @abstractmethod
    def get_application(self, application_id: str, version: str) -> Application: ...

    @abstractmethod
    def list_applications(self) -> tuple[Application, ...]: ...

    @abstractmethod
    def save_instance(self, instance: ApplicationInstance) -> ApplicationInstance: ...

    @abstractmethod
    def get_instance(self, instance_id: str) -> ApplicationInstance: ...

    @abstractmethod
    def list_instances(
        self,
        *,
        application_id: str | None = None,
    ) -> tuple[ApplicationInstance, ...]: ...


class InMemoryApplicationRepository(ApplicationRepository):
    """Deterministic reference repository used by contract and lifecycle tests."""

    def __init__(self) -> None:
        self._applications: dict[tuple[str, str], Application] = {}
        self._instances: dict[str, ApplicationInstance] = {}

    def save_application(self, application: Application) -> Application:
        key = (application.application_id, application.version)
        current = self._applications.get(key)
        if current is not None:
            same_definition = (
                current.manifest == application.manifest
                and current.runtime_id == application.runtime_id
                and current.source_ref == application.source_ref
                and dict(current.provenance) == dict(application.provenance)
            )
            if not same_definition:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "application definition is immutable for an existing application/version",
                    details={
                        "application_id": application.application_id,
                        "version": application.version,
                    },
                )
            return current
        self._applications[key] = application
        return application

    def get_application(self, application_id: str, version: str) -> Application:
        validate_id(application_id, "application")
        if not version.strip():
            raise ValueError("version must not be blank")
        try:
            return self._applications[(application_id, version)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application not found: {application_id!r} {version!r}",
            ) from exc

    def list_applications(self) -> tuple[Application, ...]:
        return tuple(
            self._applications[key]
            for key in sorted(self._applications, key=lambda item: (item[0], item[1]))
        )

    def save_instance(self, instance: ApplicationInstance) -> ApplicationInstance:
        current = self._instances.get(instance.instance_id)
        if current is not None:
            if instance.revision < current.revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "application instance revision must not move backwards",
                    details={
                        "instance_id": instance.instance_id,
                        "current_revision": current.revision,
                        "proposed_revision": instance.revision,
                    },
                )
            if instance.revision == current.revision and instance != current:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "application instance updates require a new revision",
                    details={
                        "instance_id": instance.instance_id,
                        "revision": instance.revision,
                    },
                )
        self._instances[instance.instance_id] = instance
        return instance

    def get_instance(self, instance_id: str) -> ApplicationInstance:
        validate_id(instance_id, "application_instance")
        try:
            return self._instances[instance_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application instance not found: {instance_id}",
            ) from exc

    def list_instances(
        self,
        *,
        application_id: str | None = None,
    ) -> tuple[ApplicationInstance, ...]:
        if application_id is not None:
            validate_id(application_id, "application")
        items = (
            instance
            for instance in self._instances.values()
            if application_id is None or instance.application_id == application_id
        )
        return tuple(sorted(items, key=lambda item: item.instance_id))
