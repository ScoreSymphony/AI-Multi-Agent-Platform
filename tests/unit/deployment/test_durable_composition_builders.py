from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from ai_multi_agent_platform.deployment.composition.durable import (
    build_application_distribution,
    build_automatic_review,
    build_connector_foundation,
    build_connector_services,
    build_egress_foundation,
    build_planning,
)
from ai_multi_agent_platform.deployment.composition.durable_extensions import (
    build_durable_extensions,
    register_durable_template_environment,
)
from ai_multi_agent_platform.deployment.composition.execution import StartupLifecycleBinding
from ai_multi_agent_platform.deployment.composition.foundation import (
    build_single_node_foundation,
)
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.context_operationalization import (
    install_single_node_context,
)
from ai_multi_agent_platform.deployment.durable_connectors import (
    build_single_node_deployment as build_durable_single_node_deployment,
)
from ai_multi_agent_platform.deployment.single_node import (
    build_single_node_deployment_from_foundation,
)


def test_durable_builders_compose_from_public_base_seams(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "durable", secure_cookie=False)
    foundation = build_single_node_foundation(config)
    connector_foundation = build_connector_foundation(config)
    egress = build_egress_foundation(
        config,
        foundation.security,
        foundation.observability,
    )
    base = build_single_node_deployment_from_foundation(
        config,
        foundation,
        repository_discovery_resolver=connector_foundation.repository_discovery_resolver,
        model_runtime_factory=egress.model_runtime,
    )

    review = build_automatic_review(base)
    egress_connectors = build_connector_services(base, connector_foundation, egress)
    application = build_application_distribution(
        config,
        base,
        connector_foundation,
        egress_connectors,
        enable_distributed_execution=False,
    )
    planning = build_planning(config, base)
    extensions = build_durable_extensions(
        base,
        egress=egress,
        planning=planning,
    )
    register_durable_template_environment(base, connector_foundation.registry)

    assert base.model_runtime.egress_gate is egress.runtime.gate
    assert base.pre_authorization_lifecycle is not None
    assert base.lifecycle_binding is not None
    assert review.workflow is not None
    assert egress_connectors.connectors is not None
    assert application.service.gate_coordinator is not None
    assert planning.service is not None
    assert extensions.context is not None
    assert extensions.learning is not None
    assert extensions.handoffs is not None


def test_durable_root_and_context_use_public_composition_seams() -> None:
    root_source = inspect.getsource(build_durable_single_node_deployment)
    review_source = inspect.getsource(build_automatic_review)
    context_source = inspect.getsource(install_single_node_context)
    lifecycle_binding_source = inspect.getsource(StartupLifecycleBinding)

    assert len(root_source.splitlines()) <= 150
    assert "_completion_authority" not in review_source
    assert "dataclasses.fields" not in root_source
    assert "fields(" not in root_source
    assert "model_runtime.egress_gate =" not in root_source
    assert "kernel._lifecycle" not in context_source
    assert '"_inner"' not in context_source
    assert "lifecycle_binding.bind_final" in context_source
    assert "._lifecycle" not in lifecycle_binding_source


def test_durable_full_build_restart_and_smoke_are_preserved(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "restart", secure_cookie=False)
        first = build_durable_single_node_deployment(config)
        first.bootstrap_admin("admin", "durable-composition-password")
        first_smoke = await first.run_reference_smoke()

        restarted = build_durable_single_node_deployment(config)
        restarted.bootstrap_admin("admin", "durable-composition-password")
        second_smoke = await restarted.run_reference_smoke()

        assert first.model_runtime.egress_gate is first.egress.runtime.gate
        assert restarted.model_runtime.egress_gate is restarted.egress.runtime.gate
        assert second_smoke.task_id == first_smoke.task_id
        assert second_smoke.run_id == first_smoke.run_id
        assert restarted.context.reconciliation is not None

    asyncio.run(scenario())
