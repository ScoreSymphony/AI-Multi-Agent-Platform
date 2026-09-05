from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, bootstrap_standard_agents
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, HTTPRequest, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID

_PASSWORD = "correct horse battery staple"


class EvaluationTargetTransport:
    def __init__(self, answer: str = "evaluation target answer") -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, Mapping[str, JsonValue] | None]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del headers, timeout_seconds
        self.calls.append((method, url, payload))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "qwen-eval"}]})
        if url.endswith("/chat/completions"):
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-eval",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": self.answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 3,
                        "total_tokens": 8,
                    },
                },
            )
        raise AssertionError(f"unexpected model endpoint: {url}")


def _context(user_id: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
    )


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _write_suite(config: SingleNodeConfig, name: str, payload: dict[str, JsonValue]) -> None:
    config.evaluation_suites_dir.mkdir(parents=True, exist_ok=True)
    (config.evaluation_suites_dir / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _model_payload() -> dict[str, JsonValue]:
    return {
        "adapter_id": "openai-compatible",
        "provider_id": "local-evaluation-provider",
        "model_config_id": "model-evaluation-target",
        "provider_model": "qwen-eval",
        "display_name": "Evaluation Target Model",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": False,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def test_custom_suite_and_fixture_are_loaded_into_isolated_product_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        config.prepare_directories()
        fixture_dir = config.evaluation_fixtures_dir / "hello-fixture"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "README.txt").write_text("clean fixture\n", encoding="utf-8")
        _write_suite(
            config,
            "custom-fixture.json",
            {
                "suite_id": "custom.fixture",
                "name": "Custom fixture suite",
                "version": "1.0",
                "cases": [
                    {
                        "case_id": "custom.fixture.case",
                        "name": "Fixture isolation",
                        "version": "1.0",
                        "input_template": {
                            "title": "Custom fixture evaluation",
                            "objective": "Run through an isolated canonical workspace",
                        },
                        "fixtures": ["hello-fixture"],
                        "assertions": [
                            {
                                "assertion_id": "run-succeeded",
                                "path": "run.status",
                                "operator": "eq",
                                "expected": "succeeded",
                            },
                            {
                                "assertion_id": "workspace-bound",
                                "path": "workspace.workspace_id",
                                "operator": "exists",
                            },
                        ],
                    }
                ],
            },
        )

        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-19-product-fixture",
        )
        suites = await deployment.http.handle(
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
        assert {item["id"] for item in items} >= {
            "single-node.reference.lifecycle@1.0",
            "custom.fixture@1.0",
        }

        executed = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers(token.secret, key="issue-19-custom-fixture"),
                body={
                    "resource_ref": "custom.fixture@1.0",
                    "snapshot": {"platform_version": __version__},
                    "repetitions": 2,
                },
            )
        )
        assert executed.status == 200
        assert isinstance(executed.body, dict)
        assert executed.body["status"] == "completed"
        results = executed.body["results"]
        assert isinstance(results, list)
        assert len(results) == 4
        assert all(result["outcome"] == "passed" for result in results)
        run_id = str(executed.body["id"])

        first_binding_ids = [
            event.payload["workspace_id"]
            for event in await deployment.kernel_repository.list_events()
            if event.event_type == "run.workspace_bound"
            and event.payload.get("evaluation_run_id") == run_id
        ]
        # Workspace isolation is independently evidenced by the Evaluation assertions and
        # durable Run bindings; repetitions must not reuse one exact workspace target.
        bindings = [
            result["assertions"]
            for result in results
            if result["evaluator"]["evaluator_id"] == "reference.deterministic"
        ]
        workspace_ids = {
            assertion["actual"]
            for assertion_group in bindings
            for assertion in assertion_group
            if assertion["assertion_id"] == "workspace-bound"
        }
        assert len(workspace_ids) == 2
        assert first_binding_ids == [] or len(set(first_binding_ids)) == len(first_binding_ids)

        restarted = build_single_node_deployment(config)
        detail = restarted.evaluation.get_run_detail(run_id)
        assert detail.run.status.value == "completed"
        assert len(detail.results) == 4
        assert restarted.evaluation.get_suite("custom.fixture@1.0").suite_id == "custom.fixture"

    asyncio.run(scenario())


def test_agent_model_target_runs_through_product_evaluation_and_server_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        transport = EvaluationTargetTransport()
        first = build_single_node_deployment(
            config,
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = first.bootstrap_admin("admin", _PASSWORD)
        await first.control_plane.execute_command(
            _context(admin.user_id, "issue-19-target-model"),
            "onboarding.configure-model",
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
        project = first.scopes.create_project(
            key="issue-19-target-project",
            name="Evaluation target project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = first.scopes.create_workspace(
            key="issue-19-target-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(first.agents)
        assistant = first.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="Evaluation Target Assistant",
        )
        token = first.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-19-product-target",
        )
        _write_suite(
            config,
            "agent-target.json",
            {
                "suite_id": "custom.agent-target",
                "name": "Agent target suite",
                "version": "1.0",
                "cases": [
                    {
                        "case_id": "custom.agent-target.case",
                        "name": "Exact Agent/model target",
                        "version": "1.0",
                        "input_template": {
                            "title": "Evaluate exact Agent",
                            "objective": "Produce one response through the selected Agent and model.",
                            "evaluation_target": {
                                "kind": "agent",
                                "agent_id": assistant.agent_id,
                                "agent_revision": assistant.revision,
                                "model_config_id": "model-evaluation-target",
                                "snapshot_references": [
                                    {
                                        "kind": "prompt_config",
                                        "ref_id": "evaluation-target-prompt",
                                        "version": "1.0",
                                    }
                                ],
                            },
                        },
                        "assertions": [
                            {
                                "assertion_id": "model-selected",
                                "path": "behavior.selected_model_config_id",
                                "operator": "eq",
                                "expected": "model-evaluation-target",
                            },
                            {
                                "assertion_id": "provider-selected",
                                "path": "behavior.selected_provider_id",
                                "operator": "eq",
                                "expected": "local-evaluation-provider",
                            },
                            {
                                "assertion_id": "agent-evidence",
                                "path": "agent_behavior.runs",
                                "operator": "exists",
                            },
                        ],
                    }
                ],
            },
        )

        restarted_transport = EvaluationTargetTransport()
        deployment = build_single_node_deployment(
            config,
            onboarding_model_adapters=(
                OpenAICompatibleOnboardingAdapter(transport=restarted_transport),
            ),
        )
        await deployment.control_plane.refresh_model_provider_health(
            _context(admin.user_id, "issue-19-target-health"),
            "local-evaluation-provider",
        )
        executed = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers(token.secret, key="issue-19-agent-target-run"),
                body={
                    "resource_ref": "custom.agent-target@1.0",
                    "snapshot": {"platform_version": __version__},
                },
            )
        )
        assert executed.status == 200
        assert isinstance(executed.body, dict)
        assert executed.body["status"] == "completed"
        results = executed.body["results"]
        assert isinstance(results, list)
        assert all(result["outcome"] == "passed" for result in results)
        snapshot = executed.body["snapshot"]
        refs = {(item["kind"], item["ref_id"], item["version"]) for item in snapshot["references"]}
        assert ("agent", assistant.agent_id, str(assistant.revision)) in refs
        assert ("model", "model-evaluation-target", "1") in refs
        assert any(kind == "provider" and ref_id == "local-evaluation-provider" for kind, ref_id, _ in refs)
        assert ("prompt_config", "evaluation-target-prompt", "1.0") in refs
        assert any(call[1].endswith("/chat/completions") for call in restarted_transport.calls)
        agent_runs = deployment.agents.repository.list_agent_runs(str(executed.body["run_id"])) if "run_id" in executed.body else ()
        if not agent_runs:
            detail = deployment.evaluation.get_run_detail(str(executed.body["id"]))
            canonical_run_ids = {result.run_id for result in detail.results if result.run_id is not None}
            assert len(canonical_run_ids) == 1
            agent_runs = deployment.agents.repository.list_agent_runs(next(iter(canonical_run_ids)))
        assert len(agent_runs) == 1
        assert agent_runs[0].agent.agent_id == assistant.agent_id
        assert agent_runs[0].selected_model_config_id == "model-evaluation-target"

    asyncio.run(scenario())
