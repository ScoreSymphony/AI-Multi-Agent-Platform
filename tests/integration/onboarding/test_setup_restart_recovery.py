from __future__ import annotations

import asyncio
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.distribution import DistributionService
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryItemType, RegistrySource
from ai_multi_agent_platform.onboarding import (
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    OnboardingService,
)
from ai_multi_agent_platform.onboarding.setup_registry_planning import (
    DependencyAwareBrowserFirstSetupService,
)


class _Components:
    def discovered_components(self):
        return ()

    def active_profile(self):
        return None

    def environment(self):
        raise AssertionError("environment is not needed without component-profile actions")


class _Onboarding:
    def status(self, context):
        del context
        return {
            "id": "first-run",
            "type": "onboarding_status",
            "state": "needs_model",
            "local_model_count": 0,
            "self_hosted_model_count": 0,
        }


class _Distribution:
    enabled = True

    def __init__(self, item: RegistryItem) -> None:
        self.item = item

    def search(self):
        return (self.item,)

    def get(self, item_id: str, version: str | None = None):
        if item_id != self.item.item_id or (
            version is not None and version != self.item.version
        ):
            raise LookupError(f"registry item {item_id!r}@{version!r} not found")
        return self.item

    def installed(self, item_id: str):
        del item_id
        return None


class _Commands:
    def __init__(self, *, fail_activation: bool) -> None:
        self.fail_activation = fail_activation
        self.preview_calls = 0
        self.activation_calls = 0

    async def preview(self, context, item_id, payload):
        del context, item_id, payload
        self.preview_calls += 1
        return {"activation_allowed": True}

    async def activate(self, context, item_id, payload):
        del context, item_id, payload
        self.activation_calls += 1
        if self.fail_activation:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "synthetic provisioning failure",
            )
        return {"activated": True}


def _item() -> RegistryItem:
    return RegistryItem(
        item_id="recovery-component",
        item_type=RegistryItemType.PLUGIN,
        name="Recovery Component",
        description="Registry fixture for setup restart recovery.",
        version="1.0",
        publisher="tests",
        source=RegistrySource(
            repository="https://example.invalid/recovery-component",
            package_reference="pkg:recovery-component@1.0",
        ),
        license="MIT",
        provenance="test fixture",
    )


def _context(idempotency_key: str | None = None) -> RequestContext:
    return RequestContext(
        request_id="request-1",
        correlation_id="correlation-1",
        actor=ActorContext(
            principal_ref="user-1",
            owner_type="user",
            owner_id="user-1",
            actor_type="human",
        ),
        idempotency_key=idempotency_key,
    )


def _service(tmp_path, distribution: _Distribution, commands: _Commands):
    return DependencyAwareBrowserFirstSetupService(
        cast(OnboardingComponentSetupService, _Components()),
        cast(OnboardingService, _Onboarding()),
        JsonSetupSessionStore(tmp_path / "setup-sessions.json"),
        distribution=cast(DistributionService, distribution),
        registry_commands=cast(RegistryCommandHandlers, commands),
    )


def test_failed_provisioning_survives_restart_and_retries_idempotently(tmp_path) -> None:
    distribution = _Distribution(_item())
    failing_commands = _Commands(fail_activation=True)
    first = _service(tmp_path, distribution, failing_commands)

    asyncio.run(
        first.update_session(
            _context(),
            "initial-setup",
            {
                "current_step": "components",
                "registry_items": [
                    {"item_id": "recovery-component", "version": "1.0"},
                ],
            },
        )
    )
    failed = asyncio.run(
        first.provision(
            _context("attempt-1"),
            "initial-setup",
            {},
        )
    )

    assert failed["outcome"]["state"] == "failed"
    assert failed["setup"]["readiness"]["ready"] is False
    assert failing_commands.activation_calls == 1

    succeeding_commands = _Commands(fail_activation=False)
    restarted = _service(tmp_path, distribution, succeeding_commands)
    restored = restarted.status(_context())
    restored_action = next(
        action
        for action in restored["plan"]["actions"]
        if action["component_ref"] == "recovery-component@1.0"
    )

    assert restored_action["state"] == "failed"
    assert restored["readiness"]["ready"] is False

    recovered = asyncio.run(
        restarted.provision(
            _context("attempt-2"),
            "initial-setup",
            {},
        )
    )

    assert recovered["outcome"]["state"] == "completed"
    assert recovered["replayed"] is False
    assert succeeding_commands.activation_calls == 1

    replayed = asyncio.run(
        restarted.provision(
            _context("attempt-2"),
            "initial-setup",
            {},
        )
    )

    assert replayed["replayed"] is True
    assert replayed["outcome"]["state"] == "completed"
    assert succeeding_commands.activation_calls == 1
