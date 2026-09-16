"""Canonical Application lifecycle orchestration over durable state and runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .definition import Application
from .models import (
    ApplicationDesiredState,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLogEntry,
    ApplicationObservedState,
    utc_now,
)
from .repository import ApplicationRepository
from .runtime import ApplicationRuntime, ApplicationRuntimeError, ApplicationRuntimeUnavailableError


class ApplicationRuntimeRegistry:
    """Explicit runtime-adapter registry keyed by canonical runtime identity."""

    def __init__(self, runtimes: tuple[ApplicationRuntime, ...] = ()) -> None:
        self._runtimes: dict[str, ApplicationRuntime] = {}
        for runtime in runtimes:
            self.register(runtime)

    def register(self, runtime: ApplicationRuntime) -> None:
        runtime_id = runtime.descriptor.runtime_id
        if runtime_id in self._runtimes:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"application runtime already registered: {runtime_id}",
            )
        self._runtimes[runtime_id] = runtime

    def get(self, runtime_id: str) -> ApplicationRuntime:
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise ApplicationRuntimeUnavailableError(
                f"application runtime is not registered: {runtime_id}"
            ) from exc

    def list_runtime_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._runtimes))


@dataclass(frozen=True, slots=True)
class ApplicationRecoveryFailure:
    instance_id: str
    runtime_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ApplicationRecoveryReport:
    instances: tuple[ApplicationInstance, ...]
    failures: tuple[ApplicationRecoveryFailure, ...]


class ApplicationLifecycleService:
    """Own persisted desired state and converge it through selected runtime adapters."""

    def __init__(
        self,
        repository: ApplicationRepository,
        runtimes: ApplicationRuntimeRegistry,
    ) -> None:
        self._repository = repository
        self._runtimes = runtimes

    def install(
        self,
        request: ApplicationInstallRequest,
        *,
        runtime_id: str,
        source_ref: str | None = None,
    ) -> ApplicationInstance:
        runtime = self._runtimes.get(runtime_id)
        descriptor = runtime.descriptor
        service_runtimes = {service.runtime for service in request.manifest.services}
        unsupported = service_runtimes - descriptor.supported_service_runtimes
        if unsupported:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "application runtime does not support all declared service runtimes",
                details={
                    "runtime_id": runtime_id,
                    "unsupported_service_runtimes": sorted(item.value for item in unsupported),
                },
            )
        missing_capabilities = set(request.manifest.runtime_requirements) - descriptor.capabilities
        if missing_capabilities:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "application runtime does not satisfy declared runtime requirements",
                details={
                    "runtime_id": runtime_id,
                    "missing_capabilities": sorted(missing_capabilities),
                },
            )

        prepared = runtime.prepare(request)
        if prepared.runtime_id != runtime_id:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "application runtime returned an instance with a mismatched runtime_id",
                provider_id=runtime_id,
            )
        if prepared.application_id != request.manifest.application_id:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "application runtime returned an instance for another application",
                provider_id=runtime_id,
            )
        if prepared.application_version != request.manifest.version:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "application runtime returned an instance for another application version",
                provider_id=runtime_id,
            )
        if prepared.desired_state is not ApplicationDesiredState.STOPPED:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "prepared application instances must begin with desired state stopped",
                provider_id=runtime_id,
            )

        application = Application(
            manifest=request.manifest,
            runtime_id=runtime_id,
            source_ref=source_ref,
        )
        self._repository.save_application(application)
        return self._repository.save_instance(prepared)

    def start(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        intent = self._persist_desired(instance, ApplicationDesiredState.RUNNING)
        return self._invoke_transition(application, intent, runtime.start)

    def stop(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        intent = self._persist_desired(instance, ApplicationDesiredState.STOPPED)
        return self._invoke_transition(application, intent, runtime.stop)

    def restart(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        if instance.desired_state is not ApplicationDesiredState.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                "only a running application intent can be restarted",
                details={"instance_id": instance_id},
            )
        return self._invoke_transition(application, instance, runtime.restart)

    def remove(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        intent = self._persist_desired(instance, ApplicationDesiredState.REMOVED)
        return self._invoke_transition(application, intent, runtime.remove)

    def status(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        observed = runtime.status(application.manifest, instance)
        return self._persist_observation(instance, observed)

    def health(self, instance_id: str) -> ApplicationHealthStatus:
        application, instance, runtime = self._load(instance_id)
        health = runtime.health(application.manifest, instance)
        if health is not instance.health:
            updated = replace(
                instance,
                health=health,
                revision=instance.revision + 1,
                updated_at=utc_now(),
            )
            self._repository.save_instance(updated)
        return health

    def endpoints(self, instance_id: str) -> tuple[ApplicationEndpointResolution, ...]:
        application, instance, runtime = self._load(instance_id)
        endpoints = runtime.endpoints(application.manifest, instance)
        if endpoints != instance.endpoints:
            updated = replace(
                instance,
                endpoints=endpoints,
                revision=instance.revision + 1,
                updated_at=utc_now(),
            )
            self._repository.save_instance(updated)
        return endpoints

    def logs(
        self,
        instance_id: str,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        application, instance, runtime = self._load(instance_id)
        return runtime.logs(
            application.manifest,
            instance,
            service_id=service_id,
            limit=limit,
        )

    def reconcile(self, instance_id: str) -> ApplicationInstance:
        application, instance, runtime = self._load(instance_id)
        return self._invoke_transition(application, instance, runtime.reconcile)

    def recover_all(self) -> ApplicationRecoveryReport:
        recovered: list[ApplicationInstance] = []
        failures: list[ApplicationRecoveryFailure] = []
        for instance in self._repository.list_instances():
            if instance.observed_state is ApplicationObservedState.REMOVED:
                recovered.append(instance)
                continue
            try:
                application = self._repository.get_application(
                    instance.application_id,
                    instance.application_version,
                )
                runtime = self._runtimes.get(instance.runtime_id)
                observed = runtime.recover(application.manifest, instance)
                recovered.append(self._persist_observation(instance, observed))
            except (ApplicationRuntimeError, ContractError) as exc:
                failed = self._persist_failed(instance)
                recovered.append(failed)
                failures.append(
                    ApplicationRecoveryFailure(
                        instance_id=instance.instance_id,
                        runtime_id=instance.runtime_id,
                        message=str(exc),
                    )
                )
        return ApplicationRecoveryReport(
            instances=tuple(recovered),
            failures=tuple(failures),
        )

    def _load(
        self,
        instance_id: str,
    ) -> tuple[Application, ApplicationInstance, ApplicationRuntime]:
        instance = self._repository.get_instance(instance_id)
        application = self._repository.get_application(
            instance.application_id,
            instance.application_version,
        )
        if application.runtime_id != instance.runtime_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "application definition and instance disagree on runtime identity",
                details={"instance_id": instance.instance_id},
            )
        return application, instance, self._runtimes.get(instance.runtime_id)

    def _persist_desired(
        self,
        instance: ApplicationInstance,
        desired_state: ApplicationDesiredState,
    ) -> ApplicationInstance:
        if instance.desired_state is desired_state:
            return instance
        updated = replace(
            instance,
            desired_state=desired_state,
            revision=instance.revision + 1,
            updated_at=utc_now(),
        )
        return self._repository.save_instance(updated)

    def _invoke_transition(
        self,
        application: Application,
        instance: ApplicationInstance,
        operation: object,
    ) -> ApplicationInstance:
        try:
            observed = operation(application.manifest, instance)  # type: ignore[operator]
        except ApplicationRuntimeError:
            self._persist_failed(instance)
            raise
        return self._persist_observation(instance, observed)

    def _persist_observation(
        self,
        current: ApplicationInstance,
        observed: ApplicationInstance,
    ) -> ApplicationInstance:
        self._validate_runtime_result(current, observed)
        projection = replace(
            current,
            observed_state=observed.observed_state,
            health=observed.health,
            service_states=observed.service_states,
            endpoints=observed.endpoints,
        )
        if projection == current:
            return current
        updated = replace(
            projection,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=utc_now(),
        )
        return self._repository.save_instance(updated)

    def _persist_failed(self, instance: ApplicationInstance) -> ApplicationInstance:
        if (
            instance.observed_state is ApplicationObservedState.FAILED
            and instance.health is ApplicationHealthStatus.UNHEALTHY
        ):
            return instance
        return self._repository.save_instance(
            replace(
                instance,
                observed_state=ApplicationObservedState.FAILED,
                health=ApplicationHealthStatus.UNHEALTHY,
                revision=instance.revision + 1,
                updated_at=utc_now(),
            )
        )

    @staticmethod
    def _validate_runtime_result(
        expected: ApplicationInstance,
        observed: ApplicationInstance,
    ) -> None:
        identity = (
            observed.instance_id,
            observed.application_id,
            observed.application_version,
            observed.runtime_id,
        )
        expected_identity = (
            expected.instance_id,
            expected.application_id,
            expected.application_version,
            expected.runtime_id,
        )
        if identity != expected_identity:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "application runtime changed canonical instance identity",
                provider_id=expected.runtime_id,
            )
