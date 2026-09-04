from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class EvaluationCLITransport:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, dict[str, str], dict[str, str], dict[str, object] | None]
        ] = []

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
        decoded: dict[str, object] | None = None
        if body is not None:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        query = dict(parse_qsl(parsed.query))
        normalized_headers = {key.casefold(): value for key, value in headers.items()}
        self.calls.append((method, parsed.path, query, normalized_headers, decoded))

        if method == "GET" and parsed.path == "/api/v1/evaluation-suites":
            payload: object = {
                "items": [
                    {
                        "id": "suite_test@1",
                        "type": "evaluation-suite",
                        "suite_id": "suite_test",
                        "version": "1",
                        "name": "Test suite",
                        "description": "deterministic suite",
                        "tags": ["ci"],
                        "cases": [],
                    }
                ],
                "total": 1,
                "limit": 50,
                "next_cursor": None,
            }
        elif method == "GET" and parsed.path == "/api/v1/evaluation-suites/suite_test%401":
            payload = {
                "id": "suite_test@1",
                "type": "evaluation-suite",
                "suite_id": "suite_test",
                "version": "1",
                "name": "Test suite",
                "description": "deterministic suite",
                "tags": ["ci"],
                "cases": [],
            }
        elif method == "POST" and parsed.path == "/api/v1/commands/evaluation.run":
            payload = {
                "id": "evaluation_run_test",
                "type": "evaluation-run",
                "run_id": "evaluation_run_test",
                "suite_id": "suite_test",
                "suite_version": "1",
                "status": "completed",
                "results": [],
                "comparison": None,
            }
        elif method == "GET" and parsed.path == "/api/v1/evaluation-runs/evaluation_run_test":
            payload = {
                "id": "evaluation_run_test",
                "type": "evaluation-run",
                "run_id": "evaluation_run_test",
                "status": "completed",
                "results": [{"id": "result_test", "type": "evaluation-result"}],
                "comparison": None,
            }
        elif method == "POST" and parsed.path == "/api/v1/commands/evaluation.compare":
            payload = {
                "id": "evaluation_run_test",
                "type": "evaluation-comparison",
                "current_run_id": "evaluation_run_test",
                "baseline_run_id": "evaluation_run_baseline",
                "regression_count": 0,
                "improvement_count": 0,
            }
        else:
            raise AssertionError(f"unexpected request: {method} {parsed.path}")

        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: EvaluationCLITransport,
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


def test_eval_suite_list_and_show_use_canonical_resources(tmp_path: Path) -> None:
    transport = EvaluationCLITransport()
    config = tmp_path / "cli.json"

    code, listed, error = _invoke(
        config,
        transport,
        "eval",
        "suite",
        "list",
        "--limit",
        "25",
        "--direction",
        "desc",
        "--filter",
        "tags=ci",
    )
    assert code == 0 and not error
    assert listed["data"]["total"] == 1  # type: ignore[index]
    method, path, query, _, body = transport.calls[-1]
    assert method == "GET"
    assert path == "/api/v1/evaluation-suites"
    assert query == {
        "limit": "25",
        "sort": "id",
        "direction": "desc",
        "filter[tags]": "ci",
    }
    assert body is None

    code, shown, error = _invoke(config, transport, "eval", "suite", "show", "suite_test@1")
    assert code == 0 and not error
    assert shown["data"]["id"] == "suite_test@1"  # type: ignore[index]
    assert transport.calls[-1][1] == "/api/v1/evaluation-suites/suite_test%401"


def test_eval_run_sends_explicit_snapshot_and_versioned_refs(tmp_path: Path) -> None:
    transport = EvaluationCLITransport()
    config = tmp_path / "cli.json"
    snapshot = {
        "platform_version": "0.0.1",
        "platform_commit": "abc123",
        "references": [
            {
                "kind": "model",
                "ref_id": "model_local",
                "version": "1",
                "revision": "rev-a",
            }
        ],
        "environment": [{"key": "mode", "value": "reference"}],
    }

    code, executed, error = _invoke(
        config,
        transport,
        "eval",
        "run",
        "suite_test@1",
        "--snapshot-json",
        json.dumps(snapshot),
        "--repetitions",
        "2",
        "--seed",
        "41",
        "--baseline-run-id",
        "evaluation_run_baseline",
        "--regression-policy-ref",
        "policy_ci@3",
        "--idempotency-key",
        "eval-run-test",
    )
    assert code == 0 and not error
    assert executed["data"]["id"] == "evaluation_run_test"  # type: ignore[index]

    method, path, query, headers, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/commands/evaluation.run"
    assert query == {}
    assert headers["idempotency-key"] == "eval-run-test"
    assert body == {
        "resource_ref": "suite_test@1",
        "snapshot": snapshot,
        "repetitions": 2,
        "seed": 41,
        "baseline_run_id": "evaluation_run_baseline",
        "regression_policy_ref": "policy_ci@3",
    }


def test_eval_result_show_and_compare_use_durable_run_identity(tmp_path: Path) -> None:
    transport = EvaluationCLITransport()
    config = tmp_path / "cli.json"

    code, shown, error = _invoke(
        config,
        transport,
        "eval",
        "result",
        "show",
        "evaluation_run_test",
    )
    assert code == 0 and not error
    assert shown["data"]["results"][0]["id"] == "result_test"  # type: ignore[index]
    assert transport.calls[-1][1] == "/api/v1/evaluation-runs/evaluation_run_test"

    code, compared, error = _invoke(
        config,
        transport,
        "eval",
        "compare",
        "evaluation_run_test",
        "--baseline-run-id",
        "evaluation_run_baseline",
        "--regression-policy-ref",
        "policy_ci@3",
        "--idempotency-key",
        "eval-compare-test",
    )
    assert code == 0 and not error
    assert compared["data"]["regression_count"] == 0  # type: ignore[index]
    method, path, _, headers, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/commands/evaluation.compare"
    assert headers["idempotency-key"] == "eval-compare-test"
    assert body == {
        "resource_ref": "evaluation_run_test",
        "baseline_run_id": "evaluation_run_baseline",
        "regression_policy_ref": "policy_ci@3",
    }


def test_eval_rejects_invalid_snapshot_and_repetitions_before_transport(tmp_path: Path) -> None:
    transport = EvaluationCLITransport()
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(
        config,
        transport,
        "eval",
        "run",
        "suite_test@1",
        "--snapshot-json",
        "[]",
    )
    assert code == 2
    assert not payload
    assert "snapshot-json must be a JSON object" in error
    assert transport.calls == []

    code, payload, error = _invoke(
        config,
        transport,
        "eval",
        "run",
        "suite_test@1",
        "--snapshot-json",
        '{"platform_version":"0.0.1"}',
        "--repetitions",
        "0",
    )
    assert code == 2
    assert not payload
    assert "repetitions must be greater than zero" in error
    assert transport.calls == []
