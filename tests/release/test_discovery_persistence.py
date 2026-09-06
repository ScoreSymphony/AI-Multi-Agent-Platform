import json

from ai_multi_agent_platform.release import (
    JsonDiscoveryReportStore,
    ObservedUpstream,
    ReleaseOperatorService,
    StoredDiscoveryReport,
    UpdateClassification,
    UpdateDisposition,
    evaluate_update_candidates,
    load_compatibility_inventory,
)

NOW = "2026-09-06T18:35:00Z"


def _candidate_report():
    inventory = load_compatibility_inventory()
    current = inventory.entries[0]
    report = evaluate_update_candidates(
        inventory,
        (
            ObservedUpstream(
                component=current.component,
                source_url=current.source_url,
                revision="candidate-immutable-revision",
                license=current.license,
                classifications=(UpdateClassification.FEATURE,),
            ),
        ),
        observed_at=NOW,
    )
    return inventory, report


def test_discovery_report_persists_and_reloads_after_restart(tmp_path) -> None:
    inventory, report = _candidate_report()
    store = JsonDiscoveryReportStore.for_data_dir(tmp_path)
    store.write(StoredDiscoveryReport(reviewed_at=NOW, report=report))

    restarted = ReleaseOperatorService.for_data_dir(tmp_path)
    status = restarted.status()

    assert status["update_discovery_reviewed_at"] == NOW
    discovery = status["update_discovery"]
    assert isinstance(discovery, dict)
    assert discovery["mode"] == UpdateDisposition.CURRENT.value
    candidates = discovery["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["candidate_revision"] == "candidate-immutable-revision"
    assert restarted.inventory.entries[0].revision == inventory.entries[0].revision


def test_operator_can_persist_reviewed_report_without_mutating_inventory(tmp_path) -> None:
    inventory, report = _candidate_report()
    service = ReleaseOperatorService(
        inventory=inventory,
        discovery_store=JsonDiscoveryReportStore.for_data_dir(tmp_path),
    )
    pinned = service.inventory.entries[0].revision

    service.set_discovery_report(report, reviewed_at=NOW, persist=True)

    assert service.inventory.entries[0].revision == pinned
    stored = JsonDiscoveryReportStore.for_data_dir(tmp_path).read()
    assert stored is not None
    assert stored.reviewed_at == NOW
    assert stored.report.to_dict() == report.to_dict()


def test_malformed_persisted_discovery_is_advisory_and_does_not_block_runtime(tmp_path) -> None:
    state_path = tmp_path / "db" / "release-upstream-discovery.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not-json", encoding="utf-8")

    service = ReleaseOperatorService.for_data_dir(tmp_path)
    status = service.status()

    discovery = status["update_discovery"]
    assert isinstance(discovery, dict)
    assert discovery["mode"] == UpdateDisposition.DISABLED.value
    warnings = status["operator_warnings"]
    assert isinstance(warnings, list)
    assert any("persisted upstream discovery" in warning for warning in warnings)


def test_persisted_report_detects_tampered_update_available_summary(tmp_path) -> None:
    _, report = _candidate_report()
    store = JsonDiscoveryReportStore.for_data_dir(tmp_path)
    store.write(StoredDiscoveryReport(reviewed_at=NOW, report=report))
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["report"]["update_available"] = False
    store.path.write_text(json.dumps(document), encoding="utf-8")

    service = ReleaseOperatorService.for_data_dir(tmp_path)
    warnings = service.status()["operator_warnings"]

    assert isinstance(warnings, list)
    assert any("persisted upstream discovery" in warning for warning in warnings)
