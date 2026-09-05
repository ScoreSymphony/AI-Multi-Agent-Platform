from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class DoctorTransport:
    def __init__(
        self,
        *,
        health: dict[str, Any],
        readiness_status: int = 200,
    ) -> None:
        self.health = health
        self.readiness_status = readiness_status
        self.paths: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del headers, body, timeout
        assert method == "GET"
        path = urlsplit(url).path
        self.paths.append(path)
        response_headers = {
            "x-api-version": "v1",
            "x-request-id": "request_doctor",
            "x-correlation-id": "corr_doctor",
        }
        if path.rstrip("/").endswith("/api/v1"):
            payload: dict[str, Any] = {"api_version": "v1"}
            status = 200
        elif path.endswith("/api/v1/health"):
            payload = self.health
            status = 200
        elif path.endswith("/api/v1/readiness"):
            payload = self.health
            status = self.readiness_status
        elif path.endswith("/api/v1/nodes") or path.endswith("/api/v1/workers"):
            payload = {"items": []}
            status = 200
        else:
            raise AssertionError(f"unexpected doctor probe path: {path}")
        return RawResponse(
            status=status,
            body=json.dumps(payload).encode("utf-8"),
            headers=response_headers,
        )


def _doctor(
    tmp_path: Path,
    transport: DoctorTransport,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "--retries",
            "0",
            "doctor",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def test_doctor_reports_healthy_when_control_plane_and_providers_are_healthy(
    tmp_path: Path,
) -> None:
    transport = DoctorTransport(
        health={
            "status": "healthy",
            "ready": True,
            "api_version": "v1",
            "providers": [
                {
                    "id": "provider_model",
                    "type": "model",
                    "status": "healthy",
                    "available": True,
                }
            ],
        }
    )

    code, payload, error = _doctor(tmp_path, transport)

    assert code == 0
    assert not error
    assert payload["data"]["summary"] == "healthy"
    provider_check = next(
        check for check in payload["data"]["checks"] if check.get("provider_id") == "provider_model"
    )
    assert provider_check["status"] == "healthy"


def test_doctor_reports_degraded_for_non_blocking_provider_degradation(tmp_path: Path) -> None:
    transport = DoctorTransport(
        health={
            "status": "healthy",
            "ready": True,
            "api_version": "v1",
            "providers": [
                {
                    "id": "provider_model",
                    "type": "model",
                    "status": "degraded",
                    "available": True,
                },
                {
                    "id": "provider_optional",
                    "type": "knowledge",
                    "status": "unknown",
                    "available": True,
                },
            ],
        }
    )

    code, payload, error = _doctor(tmp_path, transport)

    assert code == 1
    assert not error
    assert payload["data"]["summary"] == "degraded"
    provider_checks = [
        check for check in payload["data"]["checks"] if check.get("name") == "provider_health"
    ]
    assert [check["status"] for check in provider_checks] == ["degraded", "degraded"]


def test_doctor_reports_blocking_when_canonical_readiness_fails(tmp_path: Path) -> None:
    transport = DoctorTransport(
        health={
            "status": "healthy",
            "ready": False,
            "api_version": "v1",
            "providers": [
                {
                    "id": "provider_model",
                    "type": "model",
                    "status": "unavailable",
                    "available": True,
                }
            ],
        },
        readiness_status=503,
    )

    code, payload, error = _doctor(tmp_path, transport)

    assert code == 4
    assert not error
    assert payload["data"]["summary"] == "blocking"
    assert any(
        check.get("name") == "readiness" and check.get("status") == "blocking"
        for check in payload["data"]["checks"]
    )
    assert any(
        check.get("provider_id") == "provider_model" and check.get("status") == "blocking"
        for check in payload["data"]["checks"]
    )


def test_doctor_treats_invalid_health_schema_as_blocking(tmp_path: Path) -> None:
    transport = DoctorTransport(
        health={
            "status": "healthy",
            "ready": True,
            "api_version": "v1",
            "providers": [{"id": "provider_broken"}],
        }
    )

    code, payload, error = _doctor(tmp_path, transport)

    assert code == 4
    assert not error
    assert payload["data"]["summary"] == "blocking"
    assert any(
        check.get("name") == "provider_health" and check.get("status") == "blocking"
        for check in payload["data"]["checks"]
    )
