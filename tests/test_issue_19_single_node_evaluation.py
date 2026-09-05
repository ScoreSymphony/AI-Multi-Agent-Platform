from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

PASSWORD = "correct horse battery staple"
SUITE_REF = "single-node.reference.lifecycle@1.0"


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def test_single_node_wires_runnable_durable_evaluation_api(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("admin", PASSWORD)
        token = first.authentication.create_personal_access_token(
            admin.user_id,
            purpose="evaluation-e2e",
        )

        assert "evaluation-suites" in first.control_plane.registered_collections
        assert "evaluation-runs" in first.control_plane.registered_collections
        assert "evaluation.run" in first.control_plane.registered_commands
        assert "evaluation.compare" in first.control_plane.registered_commands
        assert (config.database_dir / "evaluation.sqlite3").is_file()

        suites = await first.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/evaluation-suites",
                headers=_headers(token.secret),
            )
        )
        assert suites.status == 200
        assert isinstance(suites.body, dict)
        items = suites.body["items"]
        assert isinstance(items, list)
        assert any(item["id"] == SUITE_REF for item in items)

        executed = await first.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers(token.secret, key="issue-19-single-node-run"),
                body={
                    "resource_ref": SUITE_REF,
                    "snapshot": {
                        "platform_version": __version__,
                        "platform_commit": "issue-19-single-node-test",
                    },
                },
            )
        )
        assert executed.status == 200
        assert isinstance(executed.body, dict)
        run_id = executed.body["id"]
        assert isinstance(run_id, str)
        assert executed.body["status"] == "completed"
        results = executed.body["results"]
        assert isinstance(results, list)
        assert {result["evaluator"]["evaluator_id"] for result in results} == {
            "reference.deterministic",
            "reference.metric-threshold",
            "reference.resource-limit",
        }
        assert all(result["outcome"] == "passed" for result in results)
        snapshot = executed.body["snapshot"]
        identities = {
            (reference["kind"], reference["ref_id"]) for reference in snapshot["references"]
        }
        assert ("evaluation_suite", "single-node.reference.lifecycle") in identities
        assert any(kind == "orchestrator" for kind, _ in identities)
        assert any(kind == "executor" for kind, _ in identities)
        assert any(kind == "evaluator" for kind, _ in identities)

        restarted = build_single_node_deployment(config)
        loaded = await restarted.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/evaluation-runs/{run_id}",
                headers=_headers(token.secret),
            )
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["id"] == run_id
        assert loaded.body["status"] == "completed"
        loaded_results = loaded.body["results"]
        assert isinstance(loaded_results, list)
        assert len(loaded_results) == 3
        assert all(result["outcome"] == "passed" for result in loaded_results)

    asyncio.run(scenario())
