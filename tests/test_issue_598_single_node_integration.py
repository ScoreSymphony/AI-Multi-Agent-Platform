from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

_PASSWORD = "issue-598-test-password-with-sufficient-length"


def test_single_node_decision_records_are_authorized_registered_searchable_and_durable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("decision-admin", _PASSWORD)
        token = first.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-598-decision-e2e",
        )
        headers = {
            "authorization": f"Bearer {token.secret}",
            "content-type": "application/json",
        }

        assert first.control_plane.decisions is not None
        assert "decision-records" in first.control_plane.registered_collections

        created = await first.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/decision-record.create",
                headers={**headers, "idempotency-key": "issue-598-create"},
                body={
                    "resource_ref": "decision-records",
                    "title": "Choose runtime baseline",
                    "subject": "runtime baseline",
                    "category": "architecture-runtime",
                    "scope_type": "platform",
                    "question": "Which runtime should be the current baseline?",
                    "alternatives": [
                        {
                            "label": "Reference runtime",
                            "status": "selected",
                            "evidence_refs": [
                                {
                                    "kind": "evaluation-run",
                                    "resource_id": "evaluation_run_issue598",
                                    "revision": 1,
                                    "digest": "sha256:issue598-evaluation",
                                }
                            ],
                        },
                        {
                            "label": "Alternative runtime",
                            "status": "rejected",
                            "trade_offs": ["higher migration cost"],
                        },
                    ],
                    "outcome": "adopt",
                    "rationale": "The current evaluation evidence favors the reference runtime.",
                    "evaluation_refs": [
                        {
                            "kind": "evaluation-run",
                            "resource_id": "evaluation_run_issue598",
                            "revision": 1,
                            "digest": "sha256:issue598-evaluation",
                        }
                    ],
                },
            )
        )
        assert created.status == 200, created.body
        assert isinstance(created.body, dict)
        decision_id = created.body["id"]
        assert isinstance(decision_id, str)
        assert created.body["actor_ref"] == admin.user_id
        assert created.body["status"] == "current"

        listed = await first.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/decision-records",
                query={"filter[status]": "current", "q": "runtime"},
                headers=headers,
            )
        )
        assert listed.status == 200, listed.body
        assert isinstance(listed.body, dict)
        assert [item["id"] for item in listed.body["items"]] == [decision_id]

        searched = await first.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"q": "runtime"},
                headers=headers,
            )
        )
        assert searched.status == 200, searched.body
        assert isinstance(searched.body, dict)
        assert any(
            item["resource_type"] == "decision-record" and item["resource_id"] == decision_id
            for item in searched.body["items"]
        )

        restarted = build_single_node_deployment(config)
        assert restarted.control_plane.decisions is not None
        persisted = restarted.control_plane.decisions.view(decision_id)
        assert persisted.record.content_digest == created.body["content_digest"]
        assert persisted.record.evaluation_refs[0].revision == 1

    asyncio.run(scenario())
