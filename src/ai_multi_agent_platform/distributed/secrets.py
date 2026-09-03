"""#34 least-privilege secret delivery at the Worker execution boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil
from typing import Protocol

from ai_multi_agent_platform.configuration import (
    SecretAccessContext,
    SecretMaterial,
    SecretProvider,
)
from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot
from ai_multi_agent_platform.security import SecretReference

from .models import WorkerJobRequest
from .registry import RegistryError


class WorkerSecretReferenceResolver(Protocol):
    """Resolve one portable WorkerJob secret ref to the canonical #34 reference object."""

    def resolve_reference(self, secret_ref: str) -> SecretReference: ...


class MappingWorkerSecretReferenceResolver:
    """Deterministic reference-only resolver suitable for local/reference composition."""

    def __init__(self, references: Mapping[str, SecretReference]) -> None:
        self._references = dict(references)
        if any(not key.strip() for key in self._references):
            raise ValueError("worker secret reference keys must not be blank")

    def resolve_reference(self, secret_ref: str) -> SecretReference:
        try:
            return self._references[secret_ref]
        except KeyError as exc:
            raise RegistryError(f"unknown Worker secret reference: {secret_ref}") from exc


@dataclass(frozen=True, slots=True)
class ResolvedWorkerSecret:
    """Ephemeral secret material delivered only to a secret-aware Worker adapter."""

    secret_ref: str
    reference: SecretReference
    material: SecretMaterial


@dataclass(frozen=True, slots=True)
class WorkerSecretBundle:
    """Per-dispatch bundle; normal repr remains redacted through SecretMaterial."""

    secrets: tuple[ResolvedWorkerSecret, ...] = ()

    def get(self, secret_ref: str) -> SecretMaterial:
        for secret in self.secrets:
            if secret.secret_ref == secret_ref:
                return secret.material
        raise KeyError(secret_ref)


class SecretAwareWorkerDispatcher(Protocol):
    """Deployment adapter that can receive ephemeral resolved secrets for one dispatch."""

    @property
    def worker_id(self) -> str: ...

    async def dispatch_with_secrets(
        self,
        job: WorkerJobRequest,
        secrets: WorkerSecretBundle,
    ) -> ExecutionHandle: ...

    async def get(self, worker_job_id: str) -> ExecutionSnapshot: ...

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot: ...


class SecretDeliveringWorkerDispatcher:
    """Resolve #34 secret references immediately before exact-Worker execution.

    Plaintext material is never copied into ``WorkerJobRequest``, runtime persistence,
    telemetry or wrapper state. The ephemeral bundle exists only for the duration of the
    downstream ``dispatch_with_secrets`` call. Security-enabled deployments should pass
    the existing ``AuthorizedSecretProvider`` so #15 is enforced by the established
    secret-provider boundary rather than by a second distributed authorization stack.
    """

    def __init__(
        self,
        dispatcher: SecretAwareWorkerDispatcher,
        provider: SecretProvider,
        resolver: WorkerSecretReferenceResolver,
        *,
        purpose: str = "worker_execution",
        default_lifetime_seconds: int = 300,
    ) -> None:
        if not purpose.strip():
            raise ValueError("worker secret delivery purpose must not be blank")
        if default_lifetime_seconds <= 0:
            raise ValueError("default secret lifetime must be greater than zero")
        self._dispatcher = dispatcher
        self._provider = provider
        self._resolver = resolver
        self._purpose = purpose
        self._default_lifetime_seconds = default_lifetime_seconds

    @property
    def worker_id(self) -> str:
        return self._dispatcher.worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        bundle = await self._resolve_bundle(job)
        return await self._dispatcher.dispatch_with_secrets(job, bundle)

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._dispatcher.get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._dispatcher.cancel(worker_job_id)

    async def _resolve_bundle(self, job: WorkerJobRequest) -> WorkerSecretBundle:
        if not job.secret_refs:
            return WorkerSecretBundle()
        lifetime = self._requested_lifetime(job)
        capability_ref = (
            job.requirements.capability_refs[0]
            if len(job.requirements.capability_refs) == 1
            else None
        )
        task_id = job.execution.subject_id if job.execution.subject_type == "task" else None
        resolved: list[ResolvedWorkerSecret] = []
        for secret_ref in job.secret_refs:
            reference = self._resolver.resolve_reference(secret_ref)
            context = SecretAccessContext(
                consumer_ref=self.worker_id,
                project_id=job.execution.context.project_id,
                workspace_id=job.workspace_ref,
                task_id=task_id,
                run_id=job.execution.run_id,
                action="worker.execute",
                capability_ref=capability_ref,
                purpose=self._purpose,
                requested_lifetime_seconds=lifetime,
            )
            material = await self._provider.resolve(reference, context)
            resolved.append(
                ResolvedWorkerSecret(
                    secret_ref=secret_ref,
                    reference=reference,
                    material=material,
                )
            )
        return WorkerSecretBundle(tuple(resolved))

    def _requested_lifetime(self, job: WorkerJobRequest) -> int:
        if job.timeout_seconds is None:
            return self._default_lifetime_seconds
        return max(1, ceil(job.timeout_seconds))
