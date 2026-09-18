from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_multi_agent_platform.cli.app import run_cli
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class MarketplaceTransport:
    def __init__(self, *, error_path: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, list[str]], dict[str, str], object]] = []
        self.error_path = error_path

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        decoded: object = None
        if body is not None:
            decoded = json.loads(body.decode("utf-8"))
        self.calls.append(
            (
                method,
                parsed.path,
                parse_qs(parsed.query),
                dict(headers),
                decoded,
            )
        )
        if self.error_path == parsed.path:
            return RawResponse(
                status=409,
                body=json.dumps(
                    {
                        "code": "conflict",
                        "category": "conflict",
                        "message": "blocked",
                        "retryable": False,
                        "request_id": "request_error",
                        "correlation_id": "corr_error",
                        "details": {"marketplace_reason": "dependency_block"},
                    }
                ).encode("utf-8"),
                headers={"x-api-version": "v1"},
            )
        return RawResponse(
            status=200,
            body=json.dumps(
                {
                    "id": "example.asset@1.2.3",
                    "kind": "tool",
                    "status": "ok",
                }
            ).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "cli.json"
    store = ProfileStore.load(path)
    store.set_profile(
        "local",
        CLIProfile(endpoint="http://control.test", principal_ref="user:test"),
    )
    store.use("local")
    store.save()
    return path


def test_marketplace_search_uses_unified_collection_and_first_class_filters(
    tmp_path: Path,
) -> None:
    transport = MarketplaceTransport()
    stdout = StringIO()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "--json",
            "marketplace",
            "search",
            "agent",
            "--limit",
            "10",
            "--sort",
            "name",
            "--kind",
            "tool",
            "--kind",
            "skill",
            "--tag",
            "developer",
            "--category",
            "tools",
            "--publisher",
            "example",
            "--source",
            "source-a",
            "--license",
            "MIT",
            "--trust",
            "reviewed",
            "--maturity",
            "stable",
            "--installed",
            "true",
            "--update-available",
            "false",
            "--compatible",
            "true",
            "--platform-version",
            "1.0.0",
        ],
        transport=transport,
        stdout=stdout,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["data"]["kind"] == "tool"
    method, path, query, headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/registry-items"
    assert body is None
    assert headers["x-principal-ref"] == "user:test"
    assert query["q"] == ["agent"]
    assert query["limit"] == ["10"]
    assert query["sort"] == ["name"]
    assert query["filter[kind]"] == ["tool,skill"]
    assert query["filter[tag]"] == ["developer"]
    assert query["filter[category]"] == ["tools"]
    assert query["filter[publisher]"] == ["example"]
    assert query["filter[source]"] == ["source-a"]
    assert query["filter[license]"] == ["MIT"]
    assert query["filter[trust]"] == ["reviewed"]
    assert query["filter[maturity]"] == ["stable"]
    assert query["filter[installed]"] == ["true"]
    assert query["filter[update_available]"] == ["false"]
    assert query["filter[compatible]"] == ["true"]
    assert query["filter[platform_version]"] == ["1.0.0"]


def test_marketplace_cli_accepts_semantic_platform_kind_without_shadow_command(
    tmp_path: Path,
) -> None:
    transport = MarketplaceTransport()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "marketplace",
            "search",
            "Hermes",
            "--kind",
            "orchestrator",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    method, path, query, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/registry-items"
    assert body is None
    assert query["q"] == ["Hermes"]
    assert query["filter[kind]"] == ["orchestrator"]


def test_marketplace_kinds_lists_registered_component_metadata(tmp_path: Path) -> None:
    transport = MarketplaceTransport()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "marketplace",
            "kinds",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    method, path, query, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/marketplace-kinds"
    assert body is None
    assert query == {
        "limit": ["200"],
        "sort": ["kind"],
        "direction": ["asc"],
    }


def test_marketplace_updates_reuses_canonical_update_filter(tmp_path: Path) -> None:
    transport = MarketplaceTransport()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "marketplace",
            "updates",
            "--kind",
            "application",
            "--source",
            "source-a",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    method, path, query, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/registry-items"
    assert body is None
    assert query["filter[update_available]"] == ["true"]
    assert query["filter[kind]"] == ["application"]
    assert query["filter[source]"] == ["source-a"]


def test_marketplace_show_and_status_share_unified_detail_surface(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()

    for command in ("show", "status"):
        code = run_cli(
            [
                "--config",
                str(config),
                "marketplace",
                command,
                "example.asset",
                "1.2.3",
                "--source",
                "source-a",
            ],
            transport=transport,
            stdout=StringIO(),
        )
        assert code == 0

    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/v1/registry-items/source-a%3A%3Aexample.asset%401.2.3"),
        ("GET", "/api/v1/registry-items/source-a%3A%3Aexample.asset%401.2.3"),
    ]


def test_marketplace_preview_and_mutations_dispatch_canonical_commands(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()

    preview_code = run_cli(
        [
            "--config",
            str(config),
            "marketplace",
            "preview",
            "example.asset",
            "1.2.3",
            "--source",
            "source-a",
            "--idempotency-key",
            "preview-key",
        ],
        transport=transport,
        stdout=StringIO(),
    )
    assert preview_code == 0

    for action in ("install", "update"):
        code = run_cli(
            [
                "--config",
                str(config),
                "--yes",
                "marketplace",
                action,
                "example.asset",
                "1.2.3",
                "--source",
                "source-a",
                "--idempotency-key",
                f"{action}-key",
            ],
            transport=transport,
            stdout=StringIO(),
        )
        assert code == 0

    uninstall_code = run_cli(
        [
            "--config",
            str(config),
            "--yes",
            "marketplace",
            "uninstall",
            "example.asset",
            "--idempotency-key",
            "uninstall-key",
        ],
        transport=transport,
        stdout=StringIO(),
    )
    assert uninstall_code == 0

    assert [call[1] for call in transport.calls] == [
        "/api/v1/commands/marketplace.preview",
        "/api/v1/commands/marketplace.install",
        "/api/v1/commands/marketplace.update",
        "/api/v1/commands/marketplace.uninstall",
    ]
    assert transport.calls[0][3]["idempotency-key"] == "preview-key"
    assert transport.calls[0][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[1][3]["idempotency-key"] == "install-key"
    assert transport.calls[1][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[2][3]["idempotency-key"] == "update-key"
    assert transport.calls[2][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[3][3]["idempotency-key"] == "uninstall-key"
    assert transport.calls[3][4] == {"resource_ref": "example.asset"}


def test_marketplace_mutations_require_global_confirmation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "marketplace",
            "install",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 2
    assert transport.calls == []
    assert "--yes" in stderr.getvalue()


def test_marketplace_json_error_preserves_canonical_machine_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)
    error_path = "/api/v1/commands/marketplace.install"
    transport = MarketplaceTransport(error_path=error_path)
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "--json",
            "--yes",
            "marketplace",
            "install",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 3
    payload = json.loads(stderr.getvalue())
    assert payload["code"] == "conflict"
    assert payload["category"] == "conflict"
    assert payload["details"]["marketplace_reason"] == "dependency_block"
