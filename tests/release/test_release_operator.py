from dataclasses import replace

from ai_multi_agent_platform.release import (
    ReleaseOperatorService,
    UpdateClassification,
    UpdateDisposition,
    evaluate_update_candidates,
)
from ai_multi_agent_platform.release.discovery import ObservedUpstream


def test_packaged_operator_status_is_read_only_and_queryable() -> None:
    service = ReleaseOperatorService.packaged_defaults()
    status = service.status()
    assert status["platform_release"]
    assert status["automatic_production_updates"] is False
    assert status["production_pin_mutation"] == "not_permitted_by_discovery"
    inventory = status["compatibility_inventory"]
    assert isinstance(inventory, dict)
    assert inventory["schema_version"] == "2"
    assert inventory["versions"] == status["versions"]
    assert inventory["components"]
    discovery = status["update_discovery"]
    assert isinstance(discovery, dict)
    assert discovery["mode"] == "disabled"


def test_operator_warns_when_any_reviewed_version_dimension_differs() -> None:
    service = ReleaseOperatorService.packaged_defaults()
    service.inventory = replace(
        service.inventory,
        versions=replace(service.inventory.versions, worker_protocol="999"),
    )
    status = service.status()
    warnings = status["operator_warnings"]
    assert isinstance(warnings, list)
    assert any("worker_protocol" in warning for warning in warnings)


def test_operator_status_surfaces_advisory_candidate_without_changing_inventory() -> None:
    service = ReleaseOperatorService.packaged_defaults()
    current = service.inventory.entries[0]
    report = evaluate_update_candidates(
        service.inventory,
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
    service.set_discovery_report(report)
    status = service.status()
    discovery = status["update_discovery"]
    assert isinstance(discovery, dict)
    candidates = discovery["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["disposition"] == UpdateDisposition.UPDATE_AVAILABLE.value
    assert service.inventory.entries[0].revision == current.revision
