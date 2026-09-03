from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityCompatibilityRequest,
    CapabilityDiscoveryRequest,
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CredentialRequirement,
    InvocationTrace,
    PolicyDecision,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import new_id


class MultiSpecProvider(CapabilityToolProvider):
    def __init__(self, provider_id: str, specs: tuple[CapabilitySpec, ...]) -> None:
        self._provider_id = provider_id
        self._specs = specs
        self.calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        capability_ids = tuple(dict.fromkeys(spec.capability_id for spec in self._specs))
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="test",
            capabilities=tuple(
                Capability(
                    name=capability_id,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for capability_id in capability_ids
            ),
            health=HealthStatus.HEALTHY,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return tuple(
            CapabilityRegistration(
                capability=spec,
                provider_id=self._provider_id,
                provider_tool_ref=f"{spec.capability_id}@{spec.version}",
            )
            for spec in self._specs
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"provider_tool_ref": invocation.tool_ref},
        )


def _context() -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-12",
        owner_type="user",
        owner_id="user-issue-12",
        project_id=new_id("project"),
    )


def _invocation(
    context: OperationContext,
    capability_id: str,
    *,
    compatibility: CapabilityCompatibilityRequest | None = None,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id="invoke-issue-12",
        capability_id=capability_id,
        compatibility=compatibility,
        arguments={},
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=context.project_id,
        ),
    )


def test_policy_aware_discovery_filters_denied_without_issue15_backend() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(
            MultiSpecProvider(
                "policy-test",
                (
                    CapabilitySpec(
                        capability_id="tool.allowed",
                        name="Allowed",
                        health=HealthStatus.HEALTHY,
                    ),
                    CapabilitySpec(
                        capability_id="tool.denied",
                        name="Denied",
                        health=HealthStatus.HEALTHY,
                    ),
                    CapabilitySpec(
                        capability_id="tool.approval",
                        name="Approval gated",
                        health=HealthStatus.HEALTHY,
                    ),
                ),
            )
        )
        request = CapabilityDiscoveryRequest(context=_context())
        seen: list[tuple[str, str]] = []

        async def policy(
            discovery: CapabilityDiscoveryRequest,
            capability: CapabilitySpec,
        ) -> PolicyDecision:
            seen.append((discovery.context.owner_id, capability.capability_id))
            if capability.capability_id == "tool.denied":
                return PolicyDecision.DENY
            if capability.capability_id == "tool.approval":
                return PolicyDecision.REQUIRE_APPROVAL
            return PolicyDecision.ALLOW

        discovered = await registry.discover_capabilities(request, policy_hook=policy)

        assert {capability.capability_id for capability in discovered} == {
            "tool.allowed",
            "tool.approval",
        }
        assert ("user-issue-12", "tool.denied") in seen

    asyncio.run(scenario())


def test_compatible_version_and_feature_selection_is_deterministic() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        provider = MultiSpecProvider(
            "version-test",
            (
                CapabilitySpec(
                    capability_id="tool.versioned",
                    name="Versioned",
                    version="1.0",
                    features=("basic",),
                    health=HealthStatus.HEALTHY,
                ),
                CapabilitySpec(
                    capability_id="tool.versioned",
                    name="Versioned",
                    version="1.5",
                    features=("basic", "structured-output"),
                    health=HealthStatus.HEALTHY,
                ),
                CapabilitySpec(
                    capability_id="tool.versioned",
                    name="Versioned",
                    version="2.0",
                    features=("basic", "structured-output"),
                    health=HealthStatus.HEALTHY,
                ),
            ),
        )
        await registry.register_provider(provider)
        compatibility = CapabilityCompatibilityRequest(
            minimum_version="1.0",
            maximum_version="2.0",
            required_features=("structured-output",),
        )

        registration, _ = registry.resolve("tool.versioned", compatibility=compatibility)
        assert registration.capability.version == "1.5"

        result = await CapabilityInvoker(registry).invoke(
            _invocation(_context(), "tool.versioned", compatibility=compatibility)
        )
        assert result.capability_version == "1.5"
        assert result.output == {"provider_tool_ref": "tool.versioned@1.5"}

    asyncio.run(scenario())


def test_incompatible_version_range_fails_canonically() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(
            MultiSpecProvider(
                "version-test",
                (
                    CapabilitySpec(
                        capability_id="tool.versioned",
                        name="Versioned",
                        version="1.0",
                        health=HealthStatus.HEALTHY,
                    ),
                    CapabilitySpec(
                        capability_id="tool.versioned",
                        name="Versioned",
                        version="2.0",
                        health=HealthStatus.HEALTHY,
                    ),
                ),
            )
        )

        with pytest.raises(ContractError) as caught:
            registry.resolve(
                "tool.versioned",
                compatibility=CapabilityCompatibilityRequest(minimum_version="3.0"),
            )

        assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert caught.value.details["available_versions"] == ["1.0", "2.0"]

    asyncio.run(scenario())


def test_ambiguous_compatibility_does_not_silently_select_version() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(
            MultiSpecProvider(
                "ambiguous-version-test",
                (
                    CapabilitySpec(
                        capability_id="tool.ambiguous-version",
                        name="Ambiguous",
                        version="1.0",
                        features=("json",),
                        health=HealthStatus.HEALTHY,
                    ),
                    CapabilitySpec(
                        capability_id="tool.ambiguous-version",
                        name="Ambiguous",
                        version="1.0.0",
                        features=("json",),
                        health=HealthStatus.HEALTHY,
                    ),
                ),
            )
        )

        with pytest.raises(ContractError) as caught:
            registry.resolve(
                "tool.ambiguous-version",
                compatibility=CapabilityCompatibilityRequest(required_features=("json",)),
            )

        assert caught.value.code is ErrorCode.CONFLICT
        assert caught.value.details["ambiguous_versions"] == ["1.0", "1.0.0"]

    asyncio.run(scenario())


def test_opaque_versions_require_exact_selection_for_compatibility() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(
            MultiSpecProvider(
                "opaque-version-test",
                (
                    CapabilitySpec(
                        capability_id="tool.opaque-version",
                        name="Opaque",
                        version="1.0",
                        health=HealthStatus.HEALTHY,
                    ),
                    CapabilitySpec(
                        capability_id="tool.opaque-version",
                        name="Opaque",
                        version="stable",
                        health=HealthStatus.HEALTHY,
                    ),
                ),
            )
        )

        with pytest.raises(ContractError) as caught:
            registry.resolve(
                "tool.opaque-version",
                compatibility=CapabilityCompatibilityRequest(minimum_version="1.0"),
            )

        assert caught.value.code is ErrorCode.CONFLICT
        assert caught.value.details["opaque_versions"] == ["stable"]

        exact, _ = registry.resolve("tool.opaque-version", version="stable")
        assert exact.capability.version == "stable"

    asyncio.run(scenario())


def test_credential_requirement_survives_discovery_and_invocation_policy() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        spec = CapabilitySpec(
            capability_id="tool.credentialed",
            name="Credentialed",
            credential_requirement=CredentialRequirement.REQUIRED,
            health=HealthStatus.HEALTHY,
        )
        provider = MultiSpecProvider("credential-test", (spec,))
        await registry.register_provider(provider)

        listed = registry.list_capabilities()
        assert listed[0].credential_requirement is CredentialRequirement.REQUIRED

        discovery_seen: list[CredentialRequirement] = []

        async def discovery_policy(
            discovery: CapabilityDiscoveryRequest,
            capability: CapabilitySpec,
        ) -> PolicyDecision:
            _ = discovery
            discovery_seen.append(capability.credential_requirement)
            return PolicyDecision.ALLOW

        discovered = await registry.discover_capabilities(
            CapabilityDiscoveryRequest(context=_context()),
            policy_hook=discovery_policy,
        )
        assert discovered[0].credential_requirement is CredentialRequirement.REQUIRED
        assert discovery_seen == [CredentialRequirement.REQUIRED]

        invocation_seen: list[CredentialRequirement] = []

        async def invocation_policy(
            invocation: CapabilityInvocation,
            capability: CapabilitySpec,
        ) -> PolicyDecision:
            _ = invocation
            invocation_seen.append(capability.credential_requirement)
            return PolicyDecision.ALLOW

        result = await CapabilityInvoker(registry, policy_hook=invocation_policy).invoke(
            _invocation(_context(), "tool.credentialed")
        )
        assert result.capability_version == "1.0"
        assert invocation_seen == [CredentialRequirement.REQUIRED]
        assert provider.calls == 1

    asyncio.run(scenario())


def test_credential_classification_contains_no_secret_backend_contract() -> None:
    capability_fields = {item.name for item in fields(CapabilitySpec)}

    assert "credential_requirement" in capability_fields
    assert "secret" not in capability_fields
    assert "secret_reference" not in capability_fields
    assert "credential_provider" not in capability_fields
    assert "credential_value" not in capability_fields
