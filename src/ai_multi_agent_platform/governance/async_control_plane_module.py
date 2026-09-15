"""Async-safe Control Plane ownership adapter for optional Governance state."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .async_runtime import AsyncGovernanceRuntime
from .control_plane import (
    GOVERNANCE_AUDIT_COLLECTION,
    GOVERNANCE_COMMANDS,
    PROPOSAL_COLLECTION,
    PROPOSAL_REVISION_COLLECTION,
    SPECIFICATION_COLLECTION,
    SPECIFICATION_REVISION_COLLECTION,
    _actor,
    _allowed,
    _allowed_spec,
    _audit_resource,
    _call_context,
    _optional_string,
    _proposal_from_payload,
    _proposal_revision_from_payload,
    _require_allowed,
    _require_allowed_spec,
    _required_int,
    _required_mapping,
    _revision_ref,
    _specification_from_payload,
    _specification_revision_from_payload,
    proposal_resource,
    proposal_revision_resource,
    proposal_search_resource,
    specification_resource,
    specification_revision_resource,
    specification_search_resource,
)
from .service import GovernanceService

GOVERNANCE_MODULE = "governance"


class AsyncProposalResourceService(ResourceService):
    def __init__(self, control_plane: ControlPlane, runtime: AsyncGovernanceRuntime) -> None:
        self._control_plane = control_plane
        self._runtime = runtime

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        visible: list[dict[str, JsonValue]] = []
        for proposal in await self._runtime.list_proposals():
            if await _allowed(self._control_plane, context, "proposal:list", proposal):
                visible.append(proposal_resource(proposal))
        return tuple(visible)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            proposal_search_resource(value) for value in await self._runtime.list_proposals()
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        proposal = await self._runtime.get_proposal(resource_id)
        await _require_allowed(self._control_plane, context, "proposal:read", proposal)
        return proposal_resource(proposal)

    async def search_result_allowed(self, context: RequestContext, resource_id: str) -> bool:
        try:
            proposal = await self._runtime.get_proposal(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return await _allowed(self._control_plane, context, "proposal:read", proposal)


class AsyncSpecificationResourceService(ResourceService):
    def __init__(self, control_plane: ControlPlane, runtime: AsyncGovernanceRuntime) -> None:
        self._control_plane = control_plane
        self._runtime = runtime

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        visible: list[dict[str, JsonValue]] = []
        for specification in await self._runtime.list_specifications():
            if await _allowed_spec(
                self._control_plane,
                context,
                "specification:list",
                specification,
            ):
                visible.append(specification_resource(specification))
        return tuple(visible)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            specification_search_resource(value)
            for value in await self._runtime.list_specifications()
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        specification = await self._runtime.get_specification(resource_id)
        await _require_allowed_spec(
            self._control_plane,
            context,
            "specification:read",
            specification,
        )
        return specification_resource(specification)

    async def search_result_allowed(self, context: RequestContext, resource_id: str) -> bool:
        try:
            specification = await self._runtime.get_specification(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return await _allowed_spec(
            self._control_plane,
            context,
            "specification:read",
            specification,
        )


class AsyncProposalRevisionResourceService(ResourceService):
    search_indexable = False

    def __init__(self, control_plane: ControlPlane, runtime: AsyncGovernanceRuntime) -> None:
        self._control_plane = control_plane
        self._runtime = runtime

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for current in await self._runtime.list_proposals():
            if not await _allowed(self._control_plane, context, "proposal:read", current):
                continue
            resources.extend(
                proposal_revision_resource(value)
                for value in await self._runtime.proposal_history(current.id)
            )
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        proposal_id, revision = _revision_ref(resource_id, "proposal")
        current = await self._runtime.get_proposal(proposal_id)
        await _require_allowed(self._control_plane, context, "proposal:read", current)
        return proposal_revision_resource(await self._runtime.get_proposal(proposal_id, revision))


class AsyncSpecificationRevisionResourceService(ResourceService):
    search_indexable = False

    def __init__(self, control_plane: ControlPlane, runtime: AsyncGovernanceRuntime) -> None:
        self._control_plane = control_plane
        self._runtime = runtime

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for current in await self._runtime.list_specifications():
            if not await _allowed_spec(
                self._control_plane,
                context,
                "specification:read",
                current,
            ):
                continue
            resources.extend(
                specification_revision_resource(value)
                for value in await self._runtime.specification_history(current.id)
            )
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        specification_id, revision = _revision_ref(resource_id, "specification")
        current = await self._runtime.get_specification(specification_id)
        await _require_allowed_spec(
            self._control_plane,
            context,
            "specification:read",
            current,
        )
        return specification_revision_resource(
            await self._runtime.get_specification(specification_id, revision)
        )


class AsyncGovernanceAuditResourceService(ResourceService):
    search_indexable = False

    def __init__(self, control_plane: ControlPlane, runtime: AsyncGovernanceRuntime) -> None:
        self._control_plane = control_plane
        self._runtime = runtime

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for event in await self._runtime.list_audit():
            if await self._control_plane._allowed(
                context,
                "governance-event:list",
                event.id,
                project_id=event.project_id,
            ):
                resources.append(_audit_resource(event))
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        for event in await self._runtime.list_audit():
            if event.id != resource_id:
                continue
            if not await self._control_plane._allowed(
                context,
                "governance-event:read",
                event.id,
                project_id=event.project_id,
            ):
                raise ContractError(ErrorCode.FORBIDDEN, "governance event is forbidden")
            return _audit_resource(event)
        raise ContractError(ErrorCode.NOT_FOUND, "governance event was not found")


def governance_control_plane_module(
    control_plane: ControlPlane,
    governance: GovernanceService,
) -> ControlPlaneModule:
    """Build the complete northbound Governance contribution on an awaitable I/O seam."""

    runtime = AsyncGovernanceRuntime(governance)

    async def proposal_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        proposal = _proposal_from_payload(payload, context)
        await _require_allowed(control_plane, context, "proposal.create", proposal)
        created = await runtime.create_proposal(proposal, actor_ref=_actor(context))
        return proposal_resource(created)

    async def proposal_revise(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _proposal_revision_from_payload(current, payload, expected)
        persisted = await runtime.revise_proposal(
            revised,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return proposal_resource(persisted)

    async def proposal_request_clarification(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_proposal(resource_ref)
        await _require_allowed(
            control_plane,
            context,
            "proposal.request-clarification",
            current,
        )
        expected = _required_int(payload, "expected_revision")
        updated = await runtime.request_clarification(
            resource_ref,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return proposal_resource(updated)

    async def proposal_dismiss(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.dismiss", current)
        expected = _required_int(payload, "expected_revision")
        dismissed = await runtime.dismiss_proposal(
            resource_ref,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return proposal_resource(dismissed)

    async def proposal_supersede(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.supersede", current)
        expected = _required_int(payload, "expected_revision")
        replacement_payload = _required_mapping(payload, "replacement")
        replacement = _proposal_from_payload(
            cast(dict[str, JsonValue], dict(replacement_payload)),
            context,
            supersedes_id=resource_ref,
        )
        await _require_allowed(control_plane, context, "proposal.create", replacement)
        superseded, created = await runtime.supersede_proposal(
            resource_ref,
            replacement,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return {
            "proposal": proposal_resource(superseded),
            "replacement": proposal_resource(created),
        }

    async def specification_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        specification = _specification_from_payload(payload, context)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.create",
            specification,
        )
        created = await runtime.create_specification(
            specification,
            actor_ref=_actor(context),
        )
        return specification_resource(created)

    async def specification_revise(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_specification(resource_ref)
        await _require_allowed_spec(control_plane, context, "specification.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _specification_revision_from_payload(current, payload, expected)
        persisted = await runtime.revise_specification(
            revised,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return specification_resource(persisted)

    async def specification_request_approval(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if payload:
            raise ContractError(ErrorCode.INVALID_REQUEST, "approval request accepts no payload")
        current = await runtime.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.request-approval",
            current,
        )
        approval = await runtime.request_approval(
            resource_ref,
            context=_call_context(context),
        )
        return {
            "id": approval.approval_id,
            "type": "approval",
            "status": approval.status.value,
            "specification_id": resource_ref,
            "specification_revision": current.revision,
            "specification_digest": current.content_digest,
            "expires_at": approval.expires_at.isoformat(),
        }

    async def specification_convert(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = await runtime.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.convert-to-task",
            current,
        )
        approval_id = _optional_string(payload, "approval_id")
        unknown = set(payload) - {"approval_id"}
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported conversion fields: {sorted(unknown)!r}",
            )
        task = await runtime.convert_to_task(
            resource_ref,
            context=replace(_call_context(context), approval_id=approval_id),
        )
        return {
            "id": task.task_id,
            "type": "task",
            "status": task.status.value,
            "project_id": task.task.project_id,
            "governance": cast(JsonValue, dict(task.task.metadata).get("governance")),
        }

    handlers = {
        "proposal.create": proposal_create,
        "proposal.revise": proposal_revise,
        "proposal.request-clarification": proposal_request_clarification,
        "proposal.dismiss": proposal_dismiss,
        "proposal.supersede": proposal_supersede,
        "specification.create": specification_create,
        "specification.revise": specification_revise,
        "specification.request-approval": specification_request_approval,
        "specification.convert-to-task": specification_convert,
    }
    if frozenset(handlers) != frozenset(GOVERNANCE_COMMANDS):
        raise RuntimeError("explicit Governance module command inventory is incomplete")

    return ControlPlaneModule(
        name=GOVERNANCE_MODULE,
        resource_services={
            PROPOSAL_COLLECTION: AsyncProposalResourceService(control_plane, runtime),
            SPECIFICATION_COLLECTION: AsyncSpecificationResourceService(control_plane, runtime),
            PROPOSAL_REVISION_COLLECTION: AsyncProposalRevisionResourceService(
                control_plane,
                runtime,
            ),
            SPECIFICATION_REVISION_COLLECTION: AsyncSpecificationRevisionResourceService(
                control_plane,
                runtime,
            ),
            GOVERNANCE_AUDIT_COLLECTION: AsyncGovernanceAuditResourceService(
                control_plane,
                runtime,
            ),
        },
        command_handlers=handlers,
    )


__all__ = ["GOVERNANCE_MODULE", "governance_control_plane_module"]
