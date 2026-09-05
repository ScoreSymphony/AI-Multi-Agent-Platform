from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class PortabilityTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del headers, timeout
        path = urlsplit(url).path
        decoded: dict[str, object] | None = None
        if body is not None:
            payload = json.loads(body)
            assert isinstance(payload, dict)
            decoded = payload
        self.calls.append((method, path, decoded))

        if path == "/api/v1/commands/portability.export":
            response: object = {"package_id": "package_demo", "compatible": True}
        elif path == "/api/v1/commands/portability.package.validate":
            response = {"package_id": "package_validated", "compatible": True}
        elif path == "/api/v1/commands/portability.preview":
            response = {"preview_id": "preview_demo", "ready": True}
        elif path == "/api/v1/commands/portability.import":
            response = {"report_id": "import_demo", "status": "succeeded"}
        elif path == "/api/v1/portability-import-reports/import_demo":
            response = {"report_id": "import_demo", "status": "succeeded"}
        else:
            raise AssertionError(f"unexpected request: {method} {path}")

        return RawResponse(
            status=200,
            body=json.dumps(response).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: PortabilityTransport,
    *arguments: str,
) -> tuple[int, dict[str, object], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def test_portability_cli_exports_and_previews_through_canonical_commands(tmp_path: Path) -> None:
    transport = PortabilityTransport()
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(
        config,
        transport,
        "portability",
        "export",
        "--resource",
        "agent:agent_a",
        "--resource",
        "agent_team:team_a",
        "--metadata-json",
        '{"purpose":"migration"}',
    )
    assert code == 0 and not error
    assert payload["data"] == {"package_id": "package_demo", "compatible": True}
    assert transport.calls[-1] == (
        "POST",
        "/api/v1/commands/portability.export",
        {
            "resource_ref": "portability",
            "resources": [
                {"resource_type": "agent", "resource_id": "agent_a"},
                {"resource_type": "agent_team", "resource_id": "team_a"},
            ],
            "metadata": {"purpose": "migration"},
        },
    )

    code, payload, error = _invoke(
        config,
        transport,
        "portability",
        "preview",
        "package_demo",
    )
    assert code == 0 and not error
    assert payload["data"] == {"preview_id": "preview_demo", "ready": True}
    assert transport.calls[-1] == (
        "POST",
        "/api/v1/commands/portability.preview",
        {"resource_ref": "package_demo"},
    )


def test_portability_cli_validates_package_files(tmp_path: Path) -> None:
    transport = PortabilityTransport()
    config = tmp_path / "cli.json"
    package_file = tmp_path / "portable.json"
    package_file.write_text(
        json.dumps({"format_version": "1", "manifest": {}, "resources": []}),
        encoding="utf-8",
    )

    code, payload, error = _invoke(
        config,
        transport,
        "portability",
        "validate",
        str(package_file),
    )
    assert code == 0 and not error
    assert payload["data"] == {"package_id": "package_validated", "compatible": True}
    assert transport.calls[-1] == (
        "POST",
        "/api/v1/commands/portability.package.validate",
        {
            "resource_ref": "portability",
            "package": {"format_version": "1", "manifest": {}, "resources": []},
        },
    )


def test_portability_cli_requires_confirmation_and_never_submits_import_plan(tmp_path: Path) -> None:
    transport = PortabilityTransport()
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(
        config,
        transport,
        "portability",
        "import",
        "preview_demo",
    )
    assert code == 2
    assert not payload
    assert "--yes" in error
    assert transport.calls == []

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "portability",
        "import",
        "preview_demo",
    )
    assert code == 0 and not error
    assert payload["data"] == {"report_id": "import_demo", "status": "succeeded"}
    assert transport.calls[-1] == (
        "POST",
        "/api/v1/commands/portability.import",
        {"resource_ref": "preview_demo"},
    )

    code, payload, error = _invoke(
        config,
        transport,
        "portability",
        "report",
        "import_demo",
    )
    assert code == 0 and not error
    assert payload["data"] == {"report_id": "import_demo", "status": "succeeded"}
    assert transport.calls[-1] == (
        "GET",
        "/api/v1/portability-import-reports/import_demo",
        None,
    )
