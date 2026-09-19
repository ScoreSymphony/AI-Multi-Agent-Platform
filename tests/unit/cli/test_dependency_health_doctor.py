from __future__ import annotations

from ai_multi_agent_platform.cli.main import _doctor_health


def test_doctor_health_keeps_optional_unavailable_dependency_degraded() -> None:
    overall, checks = _doctor_health(
        {
            "status": "healthy",
            "ready": True,
            "readiness_state": "degraded",
            "providers": [
                {
                    "id": "platform-observability-health",
                    "type": "observability-health",
                    "status": "degraded",
                    "available": True,
                    "readiness_state": "degraded",
                    "dependencies": [
                        {
                            "name": "optional-search",
                            "state": "unavailable",
                            "required": False,
                            "error_code": "timeout",
                            "attempts": 2,
                            "retry_count": 1,
                            "last_retry_error_code": "timeout",
                            "probe_duration_seconds": 0.02,
                            "degraded_duration_seconds": 1.25,
                            "failure_count": 1,
                            "recovery_count": 0,
                            "operator_action": "restore or disable the optional dependency",
                        }
                    ],
                }
            ],
        }
    )

    assert overall == "degraded"
    dependency = next(check for check in checks if check["name"] == "dependency_health")
    assert dependency["status"] == "degraded"
    assert dependency["required"] is False
    assert dependency["error_code"] == "timeout"
    assert dependency["attempts"] == 2
    assert dependency["retry_count"] == 1
    assert dependency["last_retry_error_code"] == "timeout"
    assert dependency["probe_duration_seconds"] == 0.02
    assert dependency["degraded_duration_seconds"] == 1.25
    assert dependency["failure_count"] == 1
    assert dependency["recovery_count"] == 0
    assert "restore or disable" in dependency["guidance"]


def test_doctor_health_reports_required_operator_intervention_with_guidance() -> None:
    overall, checks = _doctor_health(
        {
            "status": "healthy",
            "ready": False,
            "readiness_state": "operator_intervention_required",
            "providers": [
                {
                    "id": "platform-observability-health",
                    "type": "observability-health",
                    "status": "unavailable",
                    "available": True,
                    "readiness_state": "operator_intervention_required",
                    "dependencies": [
                        {
                            "name": "required-persistence",
                            "state": "operator_intervention_required",
                            "required": True,
                            "error_code": "contract_violation",
                            "attempts": 1,
                            "failure_count": 1,
                            "recovery_count": 0,
                        }
                    ],
                }
            ],
        }
    )

    assert overall == "blocking"
    dependency = next(check for check in checks if check["name"] == "dependency_health")
    assert dependency["status"] == "blocking"
    assert "supported operator command" in dependency["guidance"]


def test_doctor_health_rejects_unhashable_readiness_values_without_crashing() -> None:
    overall, checks = _doctor_health(
        {
            "status": "healthy",
            "ready": False,
            "readiness_state": ["invalid"],
            "providers": [],
        }
    )

    assert overall == "blocking"
    assert checks[0]["name"] == "health_schema"
    assert checks[0]["status"] == "blocking"

    overall, checks = _doctor_health(
        {
            "status": "healthy",
            "ready": False,
            "readiness_state": "unavailable",
            "providers": [
                {
                    "id": "platform-observability-health",
                    "type": "observability-health",
                    "status": "unavailable",
                    "available": True,
                    "dependencies": [
                        {
                            "name": "kernel-persistence",
                            "state": {"invalid": True},
                            "required": True,
                        }
                    ],
                }
            ],
        }
    )

    assert overall == "blocking"
    schema_check = next(check for check in checks if check["name"] == "dependency_health_schema")
    assert schema_check["status"] == "blocking"
