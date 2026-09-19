from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, quote, urlparse

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class _ParityTransport:
    def __init__(self, payloads: Mapping[str, tuple[int, object] | object]) -> None:
        self.payloads = dict(payloads)
        self.calls: list[tuple[str, str]] = []

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
        self.calls.append((method, url))
        request_path = urlparse(url).path
        for path, configured in self.payloads.items():
            if request_path != path:
                continue
            if isinstance(configured, tuple):
                status, payload = configured
            else:
                status, payload = 200, configured
            return RawResponse(
                status=status,
                body=json.dumps(payload).encode("utf-8"),
                headers={"x-api-version": "v1"},
            )
        raise AssertionError(f"unexpected client-parity URL: {url}")


def _fixture(name: str) -> dict[str, object]:
    payload = json.loads((Path("frontend/src/api/__fixtures__") / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "cli.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "current_profile": "reference",
                "profiles": {
                    "reference": {
                        "endpoint": "http://control-plane.invalid",
                        "principal_ref": "user:issue-1236-client-parity",
                        "owner_type": "user",
                        "owner_id": "issue-1236-client-parity",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def test_cli_reads_shared_canonical_task_run_result_state(tmp_path: Path) -> None:
    task = _fixture("canonical-task.json")
    run = _fixture("canonical-run.json")
    result = _fixture("canonical-result.json")
    task_id = task["id"]
    run_id = run["id"]
    result_id = result["id"]
    assert isinstance(task_id, str)
    assert isinstance(run_id, str)
    assert isinstance(result_id, str)
    assert run["task_id"] == task_id
    assert result["task_id"] == task_id
    assert task["run_ids"] == [run_id]
    assert task["result_ids"] == [result_id]
    assert task["status"] == run["status"] == "succeeded"
    assert task["correlation_id"] == run["correlation_id"]

    config = _config(tmp_path)
    transport = _ParityTransport(
        {
            f"/api/v1/tasks/{task_id}": task,
            f"/api/v1/runs/{run_id}": run,
            f"/api/v1/results/{result_id}": result,
        }
    )

    commands = (
        (("task", "show", task_id), task),
        (("run", "show", run_id), run),
        (("result", "show", result_id), result),
    )
    for command, expected in commands:
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            ("--config", str(config), "--json", *command),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )
        assert code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue())["data"] == expected

    assert transport.calls == [
        ("GET", f"http://control-plane.invalid/api/v1/tasks/{task_id}"),
        ("GET", f"http://control-plane.invalid/api/v1/runs/{run_id}"),
        ("GET", f"http://control-plane.invalid/api/v1/results/{result_id}"),
    ]


def test_cli_preserves_shared_task_query_pagination_and_error_semantics(tmp_path: Path) -> None:
    task = _fixture("canonical-task.json")
    page = _fixture("canonical-task-page.json")
    api_error = _fixture("canonical-api-error.json")
    task_id = task["id"]
    assert isinstance(task_id, str)
    assert page["items"] == [task]

    config = _config(tmp_path)
    transport = _ParityTransport(
        {
            "/api/v1/tasks": page,
            f"/api/v1/tasks/{task_id}": (403, api_error),
        }
    )

    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        (
            "--config",
            str(config),
            "--json",
            "task",
            "list",
            "--limit",
            "1",
            "--cursor",
            "cursor_client_parity",
            "--sort",
            "updated_at",
            "--direction",
            "desc",
            "--q",
            "Shared canonical",
            "--filter",
            "status=succeeded",
            "--fields",
            "id,status",
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert json.loads(stdout.getvalue())["data"] == page
    list_url = transport.calls[0][1]
    parsed = urlparse(list_url)
    assert parsed.path == "/api/v1/tasks"
    assert parse_qs(parsed.query) == {
        "limit": ["1"],
        "cursor": ["cursor_client_parity"],
        "sort": ["updated_at"],
        "direction": ["desc"],
        "q": ["Shared canonical"],
        "filter[status]": ["succeeded"],
        "fields": ["id,status"],
    }

    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ("--config", str(config), "--json", "task", "show", task_id),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 3
    assert stdout.getvalue() == ""
    observed_error = json.loads(stderr.getvalue())
    assert observed_error == {
        **api_error,
        "status": 403,
    }


def test_cli_core_lifecycle_mutations_use_the_shared_public_routes(tmp_path: Path) -> None:
    task = _fixture("canonical-task.json")
    run = _fixture("canonical-run.json")
    routes = _fixture("canonical-core-lifecycle-routes.json")
    task_id = task["id"]
    run_id = run["id"]
    assert isinstance(task_id, str)
    assert isinstance(run_id, str)

    route_payloads: dict[str, object] = {}
    for name, raw_route in routes.items():
        assert isinstance(raw_route, dict)
        path = raw_route.get("path")
        assert isinstance(path, str)
        route_payloads[path] = run if name in {"task_start", "task_retry", "run_cancel"} else task

    config = _config(tmp_path)
    transport = _ParityTransport(route_payloads)
    commands = (
        ("task", "queue", task_id),
        ("task", "start", task_id),
        ("task", "cancel", task_id),
        ("task", "retry", task_id),
        ("run", "cancel", run_id, "--task-id", task_id),
    )

    for command in commands:
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            ("--config", str(config), "--json", "--yes", *command),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )
        assert code == 0
        assert stderr.getvalue() == ""

    expected_calls: list[tuple[str, str]] = []
    for raw_route in routes.values():
        assert isinstance(raw_route, dict)
        method = raw_route.get("method")
        path = raw_route.get("path")
        assert isinstance(method, str)
        assert isinstance(path, str)
        expected_calls.append((method, f"http://control-plane.invalid{path}"))

    assert transport.calls == expected_calls


class _MutationFailureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, Mapping[str, str]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del body, timeout
        self.calls.append((method, url, dict(headers)))
        return RawResponse(
            status=503,
            body=json.dumps(self.payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def test_cli_mutation_uses_idempotency_key_and_does_not_retry_retryable_error(
    tmp_path: Path,
) -> None:
    task = _fixture("canonical-task.json")
    retryable_error = _fixture("canonical-retryable-api-error.json")
    task_id = task["id"]
    assert isinstance(task_id, str)

    config = _config(tmp_path)
    transport = _MutationFailureTransport(retryable_error)
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        (
            "--config",
            str(config),
            "--json",
            "--yes",
            "--retries",
            "5",
            "task",
            "queue",
            task_id,
            "--idempotency-key",
            "issue-1236-shared-idempotency",
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 3
    assert stdout.getvalue() == ""
    assert len(transport.calls) == 1
    method, url, headers = transport.calls[0]
    assert method == "POST"
    assert url == f"http://control-plane.invalid/api/v1/tasks/{task_id}:queue"
    assert headers["idempotency-key"] == "issue-1236-shared-idempotency"
    observed_error = json.loads(stderr.getvalue())
    assert observed_error["code"] == retryable_error["code"]
    assert observed_error["category"] == retryable_error["category"]
    assert observed_error["retryable"] is True


def test_cli_preserves_shared_canonical_core_error_semantics(tmp_path: Path) -> None:
    task = _fixture("canonical-task.json")
    cases = _fixture("canonical-error-cases.json")
    task_id = task["id"]
    assert isinstance(task_id, str)

    scenarios = (
        ("validation", ("task", "show", task_id), f"/api/v1/tasks/{task_id}", "GET"),
        (
            "invalid_cursor",
            ("task", "list", "--cursor", "not-a-valid-cursor"),
            "/api/v1/tasks",
            "GET",
        ),
        ("unauthenticated", ("task", "show", task_id), f"/api/v1/tasks/{task_id}", "GET"),
        ("forbidden", ("task", "show", task_id), f"/api/v1/tasks/{task_id}", "GET"),
        (
            "approval_required",
            ("--yes", "task", "queue", task_id),
            f"/api/v1/tasks/{task_id}:queue",
            "POST",
        ),
        ("not_found", ("task", "show", task_id), f"/api/v1/tasks/{task_id}", "GET"),
        (
            "conflict",
            ("--yes", "task", "queue", task_id),
            f"/api/v1/tasks/{task_id}:queue",
            "POST",
        ),
        ("unavailable", ("task", "show", task_id), f"/api/v1/tasks/{task_id}", "GET"),
        (
            "retryable_backend_failure",
            ("task", "show", task_id),
            f"/api/v1/tasks/{task_id}",
            "GET",
        ),
        (
            "non_retryable_backend_failure",
            ("task", "show", task_id),
            f"/api/v1/tasks/{task_id}",
            "GET",
        ),
    )
    for case_name, command, path, expected_method in scenarios:
        raw_case = cases[case_name]
        assert isinstance(raw_case, dict)
        status = raw_case["status"]
        error_body = raw_case["body"]
        assert isinstance(status, int)
        assert isinstance(error_body, dict)

        transport = _ParityTransport({path: (status, error_body)})
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            (
                "--config",
                str(_config(tmp_path)),
                "--json",
                "--retries",
                "0",
                *command,
            ),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )

        assert code == 3
        assert stdout.getvalue() == ""
        observed = json.loads(stderr.getvalue())
        assert observed["status"] == status
        assert observed["code"] == error_body["code"]
        assert observed["category"] == error_body["category"]
        assert observed["retryable"] == error_body["retryable"]
        assert observed.get("details") == error_body.get("details")
        assert transport.calls == [(expected_method, f"http://control-plane.invalid{path}")]



class _MarketplaceParityTransport:
    def __init__(self, payloads: Mapping[str, tuple[int, object] | object]) -> None:
        self.payloads = dict(payloads)
        self.calls: list[tuple[str, str, Mapping[str, str], object | None]] = []

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
        decoded_body: object | None = None
        if body:
            decoded_body = json.loads(body.decode("utf-8"))
        self.calls.append((method, url, dict(headers), decoded_body))
        request_path = urlparse(url).path
        for path, configured in self.payloads.items():
            if request_path != path:
                continue
            if isinstance(configured, tuple):
                status, payload = configured
            else:
                status, payload = 200, configured
            return RawResponse(
                status=status,
                body=json.dumps(payload).encode("utf-8"),
                headers={"x-api-version": "v1"},
            )
        raise AssertionError(f"unexpected Marketplace parity URL: {url}")


def test_cli_preserves_shared_marketplace_pagination_filter_sort_and_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture("canonical-marketplace.json")
    page = fixture["page"]
    item = fixture["item"]
    assert isinstance(page, dict)
    assert isinstance(item, dict)
    item_id = item["item_id"]
    version = item["version"]
    source = item["source_registry"]
    assert isinstance(item_id, str)
    assert isinstance(version, str)
    assert isinstance(source, str)

    config = _config(tmp_path)
    transport = _MarketplaceParityTransport({"/api/v1/registry-items": page})
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        (
            "--config",
            str(config),
            "--json",
            "marketplace",
            "search",
            "Research",
            "--limit",
            "1",
            "--cursor",
            "marketplace-parity-cursor",
            "--sort",
            "name",
            "--direction",
            "asc",
            "--kind",
            "agent",
            "--deprecated",
            "false",
            "--source",
            source,
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    observed = json.loads(stdout.getvalue())["data"]
    assert observed == page
    assert observed["items"][0]["item_id"] == item_id
    assert observed["items"][0]["qualified_id"] == item["qualified_id"]

    method, url, _headers, body = transport.calls[0]
    assert method == "GET"
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/api/v1/registry-items"
    assert parse_qs(parsed.query) == {
        "limit": ["1"],
        "cursor": ["marketplace-parity-cursor"],
        "sort": ["name"],
        "direction": ["asc"],
        "q": ["Research"],
        "filter[kind]": ["agent"],
        "filter[deprecated]": ["false"],
        "filter[source]": [source],
    }


def test_cli_marketplace_install_then_reads_shared_canonical_owner_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture("canonical-marketplace.json")
    item = fixture["item"]
    mutation = fixture["mutation"]
    installed_item = fixture["installed_item"]
    assert isinstance(item, dict)
    assert isinstance(mutation, dict)
    assert isinstance(installed_item, dict)
    item_id = item["item_id"]
    version = item["version"]
    source = item["source_registry"]
    qualified_id = item["qualified_id"]
    assert isinstance(item_id, str)
    assert isinstance(version, str)
    assert isinstance(source, str)
    assert isinstance(qualified_id, str)

    config = _config(tmp_path)
    encoded_id = quote(qualified_id, safe="")
    transport = _MarketplaceParityTransport(
        {
            "/api/v1/commands/marketplace.install": mutation,
            f"/api/v1/registry-items/{encoded_id}": installed_item,
        }
    )

    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        (
            "--config",
            str(config),
            "--json",
            "--yes",
            "marketplace",
            "install",
            item_id,
            version,
            "--source",
            source,
            "--idempotency-key",
            "marketplace-parity-idempotency",
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0
    assert stderr.getvalue() == ""
    assert json.loads(stdout.getvalue())["data"] == mutation

    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        (
            "--config",
            str(config),
            "--json",
            "marketplace",
            "show",
            item_id,
            version,
            "--source",
            source,
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0
    assert stderr.getvalue() == ""
    observed = json.loads(stdout.getvalue())["data"]
    assert observed == installed_item
    assert observed["item_id"] == item_id
    assert observed["item_type"] == item["item_type"]
    assert observed["version"] == version
    assert observed["owner_extension"]["status"] == installed_item["owner_extension"]["status"]

    method, url, headers, body = transport.calls[0]
    assert method == "POST"
    assert url == "http://control-plane.invalid/api/v1/commands/marketplace.install"
    assert headers["idempotency-key"] == "marketplace-parity-idempotency"
    assert body == {
        "resource_ref": item_id,
        "version": version,
        "source_registry": source,
    }

    method, url, _headers, body = transport.calls[1]
    assert method == "GET"
    assert url == f"http://control-plane.invalid/api/v1/registry-items/{encoded_id}"
    assert body is None


def test_cli_preserves_shared_marketplace_deprecated_and_unsupported_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture("canonical-marketplace.json")
    deprecated = fixture["deprecated_item"]
    assert isinstance(deprecated, dict)
    item_id = deprecated["item_id"]
    version = deprecated["version"]
    source = deprecated["source_registry"]
    qualified_id = deprecated["qualified_id"]
    assert isinstance(item_id, str)
    assert isinstance(version, str)
    assert isinstance(source, str)
    assert isinstance(qualified_id, str)

    encoded_id = quote(qualified_id, safe="")
    transport = _MarketplaceParityTransport({f"/api/v1/registry-items/{encoded_id}": deprecated})
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        (
            "--config",
            str(_config(tmp_path)),
            "--json",
            "marketplace",
            "show",
            item_id,
            version,
            "--source",
            source,
        ),
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    observed = json.loads(stdout.getvalue())["data"]
    assert observed == deprecated
    assert observed["deprecated"] is True
    assert observed["operation_state"] == "unsupported"
    assert observed["route_available"] is False
    assert observed["owner_extension"]["supported_operations"] == []


def test_cli_preserves_shared_marketplace_failure_categories(tmp_path: Path) -> None:
    fixture = _fixture("canonical-marketplace.json")
    item = fixture["item"]
    errors = fixture["errors"]
    assert isinstance(item, dict)
    assert isinstance(errors, dict)
    item_id = item["item_id"]
    version = item["version"]
    source = item["source_registry"]
    qualified_id = item["qualified_id"]
    assert isinstance(item_id, str)
    assert isinstance(version, str)
    assert isinstance(source, str)
    assert isinstance(qualified_id, str)

    config = _config(tmp_path)
    for name in ("authorization", "validation", "unsupported", "unavailable"):
        raw_case = errors[name]
        assert isinstance(raw_case, dict)
        status = raw_case["status"]
        body = raw_case["body"]
        assert isinstance(status, int)
        assert isinstance(body, dict)
        transport = _MarketplaceParityTransport(
            {"/api/v1/commands/marketplace.install": (status, body)}
        )
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            (
                "--config",
                str(config),
                "--json",
                "--yes",
                "--retries",
                "5",
                "marketplace",
                "install",
                item_id,
                version,
                "--source",
                source,
                "--idempotency-key",
                f"marketplace-{name}-idempotency",
            ),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )

        assert code == 3
        assert stdout.getvalue() == ""
        observed = json.loads(stderr.getvalue())
        assert observed["status"] == status
        assert observed["code"] == body["code"]
        assert observed["category"] == body["category"]
        assert observed["retryable"] == body["retryable"]
        assert len(transport.calls) == 1

    encoded_id = quote(qualified_id, safe="")
    for name in ("not_found", "conflict"):
        raw_case = errors[name]
        assert isinstance(raw_case, dict)
        status = raw_case["status"]
        body = raw_case["body"]
        assert isinstance(status, int)
        assert isinstance(body, dict)
        transport = _MarketplaceParityTransport(
            {f"/api/v1/registry-items/{encoded_id}": (status, body)}
        )
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            (
                "--config",
                str(config),
                "--json",
                "marketplace",
                "show",
                item_id,
                version,
                "--source",
                source,
            ),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )

        assert code == 3
        assert stdout.getvalue() == ""
        observed = json.loads(stderr.getvalue())
        assert observed["status"] == status
        assert observed["code"] == body["code"]
        assert observed["category"] == body["category"]
        assert observed["retryable"] == body["retryable"]
        assert len(transport.calls) == 1
