from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService, InMemoryUsageStore
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.distributed import (
    DispatchRecord,
    DistributedRegistry,
    DistributedRuntime,
)
from ai_multi_agent_platform.evaluation import ConfigurationSnapshot
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.security import ApprovalRecord, ApprovalService


class RecordingDistributedRuntime(DistributedRuntime):
    def __init__(self) -> None:
        super().__init__(DistributedRegistry())
        self.records_called = False

    def records(self) -> tuple[DispatchRecord, ...]:
        self.records_called = True
        return super().records()


def _write_evidence_suite(config: SingleNodeConfig) -> None:
    config.evaluation_suites_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite_id": "product.evidence",
        "name": "Product evidence composition",
        "version": "1.0",
        "cases": [
            {
                "case_id": "product.evidence.case",
                "name": "Product evidence sources",
                "version": "1.0",
                "input_template": {
                    "title": "Product evidence evaluation",
                    "objective": "Project source-owned evidence through the product path",
                },
                "assertions": [
                    {
                        "assertion_id": "run-succeeded",
                        "path": "run.status",
                        "operator": "eq",
                        "expected": "succeeded",
                    },
                    {
                        "assertion_id": "accounting-composed",
                        "path": "accounting_evidence.records",
                        "operator": "exists",
                    },
                    {
                        "assertion_id": "observability-composed",
                        "path": "observability_evidence.spans",
                        "operator": "exists",
                    },
                ],
            }
        ],
    }
    (config.evaluation_suites_dir / "product-evidence.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def test_single_node_evaluation_uses_product_owned_evidence_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        config.prepare_directories()
        _write_evidence_suite(config)

        accounting = AccountingService(InMemoryUsageStore())
        observability = InMemoryExporter()
        distributed = RecordingDistributedRuntime()
        deployment = build_single_node_deployment(
            config,
            accounting_service=accounting,
            observability_exporter=observability,
            distributed_runtime=distributed,
        )

        assert deployment.accounting_service is accounting
        assert deployment.observability_exporter is observability
        assert deployment.distributed_runtime is distributed
        assert deployment.control_plane.approval_gate is deployment.approval_gate

        approval_reads: list[ApprovalService] = []
        original_all = ApprovalService.all

        def recording_all(service: ApprovalService) -> tuple[ApprovalRecord, ...]:
            if service is deployment.approval_gate.approvals:
                approval_reads.append(service)
            return original_all(service)

        monkeypatch.setattr(ApprovalService, "all", recording_all)

        summary = await deployment.evaluation.run_suite(
            suite_ref="product.evidence@1.0",
            snapshot=ConfigurationSnapshot(platform_version=__version__),
        )

        deterministic_results = [
            result for result in summary.results if result.deterministic_pass is not None
        ]
        assert deterministic_results
        assert all(result.deterministic_pass is True for result in deterministic_results)
        assert approval_reads == [deployment.approval_gate.approvals]
        assert distributed.records_called is True

    asyncio.run(scenario())
