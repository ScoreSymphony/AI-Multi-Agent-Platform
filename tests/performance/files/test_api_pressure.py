from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.api_pressure import (
    APIPressureBenchmarkSpec,
    SingleNodeAPIPressureHarness,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-api-pressure.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def test_api_pressure_profile_is_correct_and_schema_valid(tmp_path: Path) -> None:
    async def run() -> None:
        report = await SingleNodeAPIPressureHarness(
            SingleNodeConfig(data_dir=tmp_path / "api-pressure", secure_cookie=False),
            platform_commit="api-pressure-test-sha",
        ).run(
            APIPressureBenchmarkSpec(
                seed_tasks=4,
                operation_count=8,
                concurrency=2,
                page_size=2,
                warmup_operations=0,
                timeout_seconds=30.0,
                safety_max_seed_tasks=8,
            )
        )

        assert report.platform_commit == "api-pressure-test-sha"
        assert report.correctness.passed is True
        assert report.errors == ()
        assert report.correctness.attempted_operations == 8
        assert report.correctness.completed_operations == 8
        assert report.correctness.failed_operations == 0
        assert report.correctness.seeded_tasks == 4
        assert report.correctness.observed_tasks == 4
        assert report.correctness.bearer_list_operations == 2
        assert report.correctness.session_list_operations == 2
        assert report.correctness.authorized_detail_operations == 2
        assert report.correctness.pagination_scan_operations == 2
        assert report.correctness.pagination_page_requests == 4
        assert report.correctness.pagination_duplicate_ids == 0
        assert report.correctness.pagination_incomplete_scans == 0
        assert report.correctness.measured_http_requests == 10
        assert report.correctness.unique_request_ids == 10
        assert report.operation_latency.count == 8
        assert report.bearer_list_latency.count == 2
        assert report.session_list_latency.count == 2
        assert report.authorized_detail_latency.count == 2
        assert report.pagination_scan_latency.count == 2
        assert report.pagination_page_latency.count == 4
        assert report.benchmark.expected_pages_per_scan == 2
        assert report.throughput_operations_per_second > 0
        assert report.resources.storage_bytes_before >= 0
        assert report.resources.storage_bytes_after >= 0
        assert report.resources.storage_growth_bytes == (
            report.resources.storage_bytes_after - report.resources.storage_bytes_before
        )
        assert len(report.sample_task_ids) == 4

        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seed_tasks": 0}, "seed_tasks"),
        ({"operation_count": 3}, "operation_count"),
        ({"concurrency": 0}, "concurrency"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": 201}, "page_size"),
        ({"warmup_operations": -1}, "warmup_operations"),
        ({"timeout_seconds": 0.0}, "timeout_seconds"),
        ({"safety_max_seed_tasks": 0}, "safety_max_seed_tasks"),
        ({"seed_tasks": 9, "safety_max_seed_tasks": 8}, "safety bound"),
        ({"repetition_count": 2}, "one API pressure report"),
    ],
)
def test_api_pressure_spec_rejects_invalid_bounds(
    kwargs: dict[str, int | float],
    message: str,
) -> None:
    values: dict[str, int | float] = {
        "seed_tasks": 4,
        "operation_count": 8,
        "concurrency": 2,
        "page_size": 2,
        "warmup_operations": 0,
        "timeout_seconds": 30.0,
        "safety_max_seed_tasks": 8,
        "repetition_count": 1,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        APIPressureBenchmarkSpec(**values)  # type: ignore[arg-type]


def test_api_pressure_requires_fresh_data_root(tmp_path: Path) -> None:
    root = tmp_path / "occupied"
    root.mkdir()
    (root / "existing.txt").write_text("occupied", encoding="utf-8")

    async def run() -> None:
        with pytest.raises(ValueError, match="fresh empty data root"):
            await SingleNodeAPIPressureHarness(
                SingleNodeConfig(data_dir=root, secure_cookie=False)
            ).run(
                APIPressureBenchmarkSpec(
                    seed_tasks=4,
                    operation_count=4,
                    concurrency=1,
                    page_size=2,
                    warmup_operations=0,
                    timeout_seconds=30.0,
                    safety_max_seed_tasks=4,
                )
            )

    asyncio.run(run())
