"""Default distribution composition for the single-node operator entrypoint.

Concrete adapter selection belongs at this outer composition boundary, never in canonical core
contracts. The OpenAI-compatible onboarding bridge is installed here without choosing a model,
endpoint, provider account or paid service on the operator's behalf.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.server import main as run_server
from ai_multi_agent_platform.deployment.single_node import (
    SingleNodeDeployment,
    build_single_node_deployment,
)
from ai_multi_agent_platform.repositories import RepositoryCapabilityProvider

from .onboarding_openai_compatible import OpenAICompatibleOnboardingAdapter


def _repository_actor_ref(context: OperationContext) -> str:
    """Resolve the canonical owner identity used by repository authorization.

    Repository capabilities must never invent a privileged fallback identity when an invocation
    is missing canonical ownership metadata. Agent/runtime invocations therefore enter the
    RepositoryService policy boundary as the OperationContext owner or fail closed.
    """

    if context.owner_id is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository capability invocation requires an owner identity",
        )
    return context.owner_id


def build_default_single_node_deployment(config: SingleNodeConfig) -> SingleNodeDeployment:
    """Build the shipped single-node profile with installed adapter bridges only."""

    secrets = LocalSecretProvider()
    deployment = build_single_node_deployment(
        config,
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(secret_provider=secrets),),
        secret_provider=secrets,
    )
    asyncio.run(
        deployment.capabilities.register_provider(
            RepositoryCapabilityProvider(
                deployment.repositories,
                actor_resolver=_repository_actor_ref,
            )
        )
    )
    return deployment


def main(argv: Sequence[str] | None = None) -> int:
    return run_server(argv, deployment_builder=build_default_single_node_deployment)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
