import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import HTTPRequest, RELEASE_STATUS_PATH, build_openapi
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.release import (
    ObservedUpstream,
    UpdateClassification,
    evaluate_update_candidates,
)


def test_openapi_exposes_read_only_release_status_policy() -> None:
    specification = build_openapi()
    assert RELEASE_STATUS_PATH in specification["paths"]
    operation = specification["paths"][RELEASE_STATUS_PATH]["get"]
    assert operation["operationId"] == "getReleaseStatus"
    assert specification["x-release-update-policy"] == {
        "discovery": "advisory-only",
        "automatic_production_updates": False,
        "production_pin_mutation": "not_exposed_by_control_plane",
    }


def test_single_node_release_status_requires_authentication_and_surfaces_candidate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )

        anonymous = await deployment.http.handle(
            HTTPRequest(method="GET", path=RELEASE_STATUS_PATH)
        )
        assert anonymous.status == 401

        admin = deployment.bootstrap_admin("admin", "test-only-admin-password-12345")
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="release-status-regression",
        )
        operator = deployment.http.release_operator
        current = operator.inventory.entries[0]
        operator.set_discovery_report(
            evaluate_update_candidates(
                operator.inventory,
                (
                    ObservedUpstream(
                        component=current.component,
                        source_url=current.source_url,
                        revision="candidate-immutable-revision",
                        license=current.license,
                        classifications=(UpdateClassification.FEATURE,),
                    ),
                ),
                observed_at="2026-09-06T00:00:00Z",
            )
        )

        response = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=RELEASE_STATUS_PATH,
                headers={"authorization": f"Bearer {credential.secret}"},
            )
        )
        assert response.status == 200
        assert isinstance(response.body, dict)
        assert response.body["automatic_production_updates"] is False
        assert response.body["production_pin_mutation"] == "not_permitted_by_discovery"
        discovery = response.body["update_discovery"]
        assert isinstance(discovery, dict)
        assert discovery["update_available"] is True
        candidates = discovery["candidates"]
        assert isinstance(candidates, list)
        assert candidates[0]["candidate_revision"] == "candidate-immutable-revision"
        inventory = response.body["compatibility_inventory"]
        assert isinstance(inventory, dict)
        components = inventory["components"]
        assert isinstance(components, list)
        assert components[0]["revision"] == current.revision

    asyncio.run(scenario())
