"""Authorization-enforced provider wrappers for canonical execution/data/tool boundaries.

Raw providers are adapter plumbing. Application/control-plane code should expose these
wrappers (or an equivalent server-side enforcement point) rather than handing an agent a
provider that can be invoked without policy evaluation.
"""

from __future__ import annotations

from datetime import datetime

from ai_multi_agent_platform.configuration.secrets import (
    SecretAccessContext,
    SecretAuditSink,
    SecretMaterial,
    SecretMetadata,
    SecretProvider,
)
from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    FileProvider,
    JsonValue,
    KnowledgeHit,
    KnowledgeProvider,
    KnowledgeQuery,
    LifecycleBackend,
    MemoryProvider,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
    ToolInvocation,
    ToolProvider,
    ToolResult,
)
from ai_multi_agent_platform.security.types import SecretReference

from .authorization import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate


def _actor_from_operation(context: OperationContext) -> ActorIdentity:
    if context.owner_id is not None:
        actor_ref = context.owner_id
        if (
            context.owner_type is not None
            and ":" not in actor_ref
            and not actor_ref.startswith((f"{context.owner_type}_",))
        ):
            actor_ref = f"{context.owner_type}:{actor_ref}"
        return infer_actor_identity(actor_ref)
    return infer_actor_identity("service:platform")


def _operation_action(
    context: OperationContext,
    *,
    action: AuthorizationAction,
    resource_type: ResourceType,
    resource_id: str,
    payload: JsonValue = None,
    capability_ref: str | None = None,
    side_effect: str | None = None,
) -> ProposedAction:
    return ProposedAction(
        AuthorizationContext(
            actor=_actor_from_operation(context),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            operation=context,
            capability_ref=capability_ref,
            side_effect=side_effect,
        ),
        payload=payload,
    )


class AuthorizedToolProvider(ToolProvider):
    """Protect direct tool-provider access even outside the richer capability registry."""

    def __init__(self, inner: ToolProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        await self._gate.enforce(
            _operation_action(
                invocation.context,
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.TOOL,
                resource_id=invocation.tool_ref,
                payload=invocation.arguments_json(),
                side_effect="provider_declared_or_unknown",
            )
        )
        return await self._inner.invoke(invocation)


class AuthorizedLifecycleBackend(LifecycleBackend):
    """Protect Run start/read/cancel at the lifecycle provider boundary."""

    def __init__(self, inner: LifecycleBackend, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        await self._gate.enforce(
            _operation_action(
                request.context,
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.RUN,
                resource_id=request.run_id,
                payload=request.input,
                side_effect="execution",
            )
        )
        return await self._inner.start(request)

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.RUN,
                resource_id=run_id,
            )
        )
        return await self._inner.get(run_id, context)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.RUN,
                resource_id=run_id,
                side_effect="cancel_execution",
            )
        )
        return await self._inner.cancel(run_id, context)


class AuthorizedFileProvider(FileProvider):
    """Protect the original core file-provider contract."""

    def __init__(self, inner: FileProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.FILE,
                resource_id=object_ref,
                payload={"size_bytes": len(data), "metadata": metadata or {}},
                side_effect="local_write",
            )
        )
        return await self._inner.write(object_ref, data, context, metadata=metadata)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.FILE,
                resource_id=object_ref,
            )
        )
        return await self._inner.read(object_ref, context)


class AuthorizedMemoryProvider(MemoryProvider):
    """Protect the original core memory-provider contract."""

    def __init__(self, inner: MemoryProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.MEMORY,
                resource_id=f"{namespace}:{key}",
                payload={"value": value, "metadata": metadata or {}},
                side_effect="memory_write",
            )
        )
        return await self._inner.put(namespace, key, value, context, metadata=metadata)

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.MEMORY,
                resource_id=f"{namespace}:{key}",
            )
        )
        return await self._inner.get(namespace, key, context)


class AuthorizedKnowledgeProvider(KnowledgeProvider):
    """Protect the original core knowledge-provider contract."""

    def __init__(self, inner: KnowledgeProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_ref,
                payload={"content": content},
                side_effect="knowledge_write",
            )
        )
        return await self._inner.index(source_ref, content, context)

    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        await self._gate.enforce(
            _operation_action(
                request.context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id="knowledge:*",
                payload={"query": request.query, "filters": request.filters},
            )
        )
        return await self._inner.query(request)

    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit:
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_ref,
            )
        )
        return await self._inner.get(source_ref, context)


class AuthorizedSecretProvider(SecretProvider):
    """Protect secret metadata/lifecycle and resolution without auditing secret material."""

    def __init__(self, inner: SecretProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    def set_audit_hook(self, audit_hook: SecretAuditSink | None) -> None:
        self._inner.set_audit_hook(audit_hook)

    async def create(
        self,
        reference: SecretReference,
        value: str,
        *,
        purpose: str,
        allowed_consumers: tuple[str, ...] = (),
        allowed_purposes: tuple[str, ...] = (),
        expires_at: datetime | None = None,
    ) -> SecretMetadata:
        context = OperationContext(
            correlation_id=f"secret:{reference.secret_id}",
            owner_type="service",
            owner_id="platform",
        )
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MANAGE_CREDENTIALS,
                resource_type=ResourceType.SECRET_REFERENCE,
                resource_id=_secret_ref(reference),
                payload={
                    "purpose": purpose,
                    "allowed_consumers": list(allowed_consumers),
                    "allowed_purposes": list(allowed_purposes),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
                side_effect="credential_create",
            )
        )
        return await self._inner.create(
            reference,
            value,
            purpose=purpose,
            allowed_consumers=allowed_consumers,
            allowed_purposes=allowed_purposes,
            expires_at=expires_at,
        )

    async def resolve(
        self,
        reference: SecretReference,
        context: SecretAccessContext,
    ) -> SecretMaterial:
        operation = OperationContext(
            correlation_id=context.run_id or context.task_id or f"secret:{reference.secret_id}",
            owner_type="service",
            owner_id=context.consumer_ref,
            project_id=context.project_id,
        )
        await self._gate.enforce(
            ProposedAction(
                AuthorizationContext(
                    actor=infer_actor_identity(context.consumer_ref),
                    action=AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY,
                    resource_type=ResourceType.SECRET_REFERENCE,
                    resource_id=_secret_ref(reference),
                    operation=operation,
                    workspace_id=context.workspace_id,
                    task_id=context.task_id,
                    run_id=context.run_id,
                    capability_ref=context.capability_ref,
                    side_effect="credential_resolution",
                ),
                payload={
                    "purpose": context.purpose,
                    "requested_lifetime_seconds": context.requested_lifetime_seconds,
                },
            )
        )
        return await self._inner.resolve(reference, context)

    async def rotate(self, reference: SecretReference, value: str) -> SecretMetadata:
        await self._authorize_secret_management(reference, "credential_rotate")
        return await self._inner.rotate(reference, value)

    async def revoke(self, reference: SecretReference) -> SecretMetadata:
        await self._authorize_secret_management(reference, "credential_revoke")
        return await self._inner.revoke(reference)

    async def delete(self, reference: SecretReference) -> None:
        await self._authorize_secret_management(reference, "credential_delete")
        await self._inner.delete(reference)

    async def metadata(self, reference: SecretReference) -> SecretMetadata:
        context = OperationContext(
            correlation_id=f"secret:{reference.secret_id}",
            owner_type="service",
            owner_id="platform",
        )
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.SECRET_REFERENCE,
                resource_id=_secret_ref(reference),
            )
        )
        return await self._inner.metadata(reference)

    async def _authorize_secret_management(
        self,
        reference: SecretReference,
        side_effect: str,
    ) -> None:
        context = OperationContext(
            correlation_id=f"secret:{reference.secret_id}",
            owner_type="service",
            owner_id="platform",
        )
        await self._gate.enforce(
            _operation_action(
                context,
                action=AuthorizationAction.MANAGE_CREDENTIALS,
                resource_type=ResourceType.SECRET_REFERENCE,
                resource_id=_secret_ref(reference),
                side_effect=side_effect,
            )
        )


def _secret_ref(reference: SecretReference) -> str:
    return f"{reference.provider}:{reference.secret_id}"
