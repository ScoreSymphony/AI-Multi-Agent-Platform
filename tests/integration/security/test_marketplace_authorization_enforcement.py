from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.distribution import (
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MARKETPLACE_INSTALL_COMMAND,
    MarketplaceKindHandlerRegistry,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    register_distribution_control_plane,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationOutcome,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

pytestmark = pytest.mark.asyncio


class _ValidationResolver:
    async def resolve(self, context: RequestContext) -> ValidationContext:
        del context
        return ValidationContext("0.0.1")


class _RecordingSkillOwner:
    kind = RegistryItemType.SKILL

    def __init__(self) -> None:
        self.calls: list[str] = []

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"kind": item.kind}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(f"install:{item.item_id}")
        return item.item_id

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(f"update:{item.item_id}")
        return item.item_id

    async def uninstall(self, item: RegistryItem) -> object:
        self.calls.append(f"uninstall:{item.item_id}")
        return None

    async def status(self, item: RegistryItem) -> object:
        return {"item_id": item.item_id}

    def describe(self, item: RegistryItem) -> dict[str, object]:
        return {"kind": item.kind}


def _item() -> RegistryItem:
    return RegistryItem(
        item_id="acceptance.secured-skill",
        item_type=RegistryItemType.SKILL,
        name="Secured Marketplace Skill",
        description="Authorization acceptance fixture",
        version="1.0.0",
        publisher="acceptance",
        source=RegistrySource(
            "https://example.invalid/security",
            "acceptance.secured-skill@1.0.0",
        ),
        license="MIT",
        provenance="acceptance-release",
        trust_status=TrustStatus.REVIEWED,
    )


def _control_plane(
    tmp_path,
    authorization,
    *,
    item: RegistryItem | None = None,
    artifact: bytes = b"secured-skill",
    store_name: str = "marketplace-security.json",
) -> tuple[ControlPlane, _RecordingSkillOwner, JsonRegistryInstallationStore, AuthorizationGate]:
    resolved_item = item or _item()
    owner = _RecordingSkillOwner()
    installations = JsonRegistryInstallationStore(tmp_path / store_name)
    distribution = DistributionService(
        LocalRegistryProvider(
            (resolved_item,),
            {(resolved_item.item_id, resolved_item.version): artifact},
        ),
        installations=installations,
        kind_handlers=MarketplaceKindHandlerRegistry((owner,)),
    )
    gate = AuthorizationGate(authorization)
    bridge = ControlPlaneAuthorizationBridge(gate)
    events = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        ),
        events=events,
        authorization=bridge,
    )
    register_distribution_control_plane(
        control_plane,
        distribution,
        validation_context_resolver=_ValidationResolver(),
    )
    return control_plane, owner, installations, gate


async def test_marketplace_install_requires_canonical_approval_before_owner_mutation(tmp_path) -> None:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:approval",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                approval_actions=frozenset({AuthorizationAction.CREATE}),
            ),
        )
    )
    control_plane, owner, installations, gate = _control_plane(tmp_path, provider)
    item = _item()
    context = RequestContext(
        "marketplace-approval-request",
        "marketplace-approval-correlation",
        actor=ActorContext(principal_ref="user:approval", actor_type="human"),
        idempotency_key="marketplace-approval-install",
    )

    with pytest.raises(ContractError) as blocked:
        await control_plane.execute_command(
            context,
            MARKETPLACE_INSTALL_COMMAND,
            item.item_id,
            {"version": item.version},
        )

    assert blocked.value.code is ErrorCode.FORBIDDEN
    assert owner.calls == []
    assert installations.get(item.item_id) is None
    assert gate.audit_records[-1].outcome is AuthorizationOutcome.REQUIRE_APPROVAL


async def test_marketplace_install_resumes_after_exact_canonical_approval(tmp_path) -> None:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:approval-retry",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                approval_actions=frozenset({AuthorizationAction.CREATE}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:approver",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
            ),
        )
    )
    control_plane, owner, installations, gate = _control_plane(tmp_path, provider)
    item = _item()
    context = RequestContext(
        "marketplace-approval-retry-request",
        "marketplace-approval-retry-correlation",
        actor=ActorContext(principal_ref="user:approval-retry", actor_type="human"),
        idempotency_key="marketplace-approval-retry-install",
    )

    with pytest.raises(ContractError) as blocked:
        await control_plane.execute_command(
            context,
            MARKETPLACE_INSTALL_COMMAND,
            item.item_id,
            {"version": item.version},
        )
    assert blocked.value.code is ErrorCode.FORBIDDEN
    pending = gate.approvals.all()
    assert len(pending) == 1

    await gate.decide_approval(
        pending[0].approval_id,
        approver=ActorIdentity("user:approver", ActorType.HUMAN),
        approve=True,
        operation=OperationContext(correlation_id="marketplace-approval-decision"),
    )

    result = await control_plane.execute_command(
        context,
        MARKETPLACE_INSTALL_COMMAND,
        item.item_id,
        {"version": item.version},
    )

    assert result["status"] == "applied"
    assert owner.calls == [f"install:{item.item_id}"]
    assert installations.get(item.item_id) is not None
    assert gate.audit_records[-1].outcome is AuthorizationOutcome.ALLOW


async def test_marketplace_install_denial_stops_before_owner_mutation(tmp_path) -> None:
    provider = LocalAuthorizationProvider()
    control_plane, owner, installations, gate = _control_plane(tmp_path, provider)
    item = _item()
    context = RequestContext(
        "marketplace-deny-request",
        "marketplace-deny-correlation",
        actor=ActorContext(principal_ref="user:denied", actor_type="human"),
        idempotency_key="marketplace-denied-install",
    )

    with pytest.raises(ContractError) as blocked:
        await control_plane.execute_command(
            context,
            MARKETPLACE_INSTALL_COMMAND,
            item.item_id,
            {"version": item.version},
        )

    assert blocked.value.code is ErrorCode.FORBIDDEN
    assert owner.calls == []
    assert installations.get(item.item_id) is None
    assert gate.audit_records[-1].outcome is AuthorizationOutcome.DENY

async def test_marketplace_approval_digest_binds_resolved_permission_state(tmp_path) -> None:
    policy = (
        LocalPrincipalPolicy(
            principal_ref="user:approval-digest",
            actor_types=frozenset({ActorType.HUMAN}),
            allowed_actions=frozenset({AuthorizationAction.READ}),
            approval_actions=frozenset({AuthorizationAction.CREATE}),
        ),
    )
    baseline = _item()
    escalated = replace(
        baseline,
        requested_permissions=frozenset({"filesystem.write"}),
    )
    context = RequestContext(
        "marketplace-digest-request",
        "marketplace-digest-correlation",
        actor=ActorContext(principal_ref="user:approval-digest", actor_type="human"),
        idempotency_key="marketplace-digest-install",
    )

    baseline_control_plane, _, _, baseline_gate = _control_plane(
        tmp_path,
        LocalAuthorizationProvider(policy),
        item=baseline,
        store_name="baseline-marketplace-security.json",
    )
    escalated_control_plane, _, _, escalated_gate = _control_plane(
        tmp_path,
        LocalAuthorizationProvider(policy),
        item=escalated,
        store_name="escalated-marketplace-security.json",
    )

    for control_plane in (baseline_control_plane, escalated_control_plane):
        with pytest.raises(ContractError) as blocked:
            await control_plane.execute_command(
                context,
                MARKETPLACE_INSTALL_COMMAND,
                baseline.item_id,
                {"version": baseline.version},
            )
        assert blocked.value.code is ErrorCode.FORBIDDEN

    baseline_record = baseline_gate.audit_records[-1]
    escalated_record = escalated_gate.audit_records[-1]
    assert baseline_record.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert escalated_record.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    assert baseline_record.requested_action_digest is not None
    assert escalated_record.requested_action_digest is not None
    assert baseline_record.requested_action_digest != escalated_record.requested_action_digest

