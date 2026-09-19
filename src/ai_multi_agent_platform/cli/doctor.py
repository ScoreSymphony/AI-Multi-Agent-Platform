"""Health payload evaluation helpers for ``platform doctor``."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue


_DOCTOR_READINESS_STATES = frozenset(
    {
        "ready",
        "degraded",
        "reconciling",
        "unavailable",
        "operator_intervention_required",
        "draining",
    }
)
_DOCTOR_STATUS_RANK = {"healthy": 0, "degraded": 1, "blocking": 2}


def _doctor_health(body: JsonValue) -> tuple[str, list[JsonValue]]:
    if not isinstance(body, dict):
        return "blocking", [
            {
                "name": "health_schema",
                "status": "blocking",
                "message": "health payload must be a JSON object",
            }
        ]

    ready = body.get("ready")
    providers = body.get("providers")
    readiness_state = body.get("readiness_state")
    if (
        not isinstance(ready, bool)
        or not isinstance(providers, list)
        or (
            readiness_state is not None
            and (
                not isinstance(readiness_state, str)
                or readiness_state not in _DOCTOR_READINESS_STATES
            )
        )
    ):
        return "blocking", [
            {
                "name": "health_schema",
                "status": "blocking",
                "message": (
                    "health payload must contain boolean ready, provider list and a valid "
                    "readiness_state when present"
                ),
            }
        ]

    overall = "healthy" if ready else "blocking"
    checks: list[JsonValue] = []
    if isinstance(readiness_state, str):
        state_status = _doctor_readiness_status(readiness_state, ready=ready)
        overall = _merge_doctor_status(overall, state_status)
        checks.append(
            {
                "name": "readiness_state",
                "status": state_status,
                "readiness_state": readiness_state,
                "guidance": _doctor_guidance(readiness_state, required=not ready),
            }
        )

    for provider in providers:
        provider_status, provider_checks = _doctor_provider_health(
            provider,
            platform_ready=ready,
        )
        overall = _merge_doctor_status(overall, provider_status)
        checks.extend(provider_checks)
    return overall, checks


def _doctor_provider_health(
    provider: JsonValue,
    *,
    platform_ready: bool,
) -> tuple[str, list[JsonValue]]:
    if not isinstance(provider, dict):
        return "blocking", [
            {
                "name": "provider_health",
                "status": "blocking",
                "message": "provider health entry must be a JSON object",
            }
        ]

    provider_id = provider.get("id")
    provider_type = provider.get("type")
    status = provider.get("status")
    available = provider.get("available")
    diagnostics = provider.get("diagnostics", [])
    if (
        not isinstance(provider_id, str)
        or not isinstance(provider_type, str)
        or not isinstance(status, str)
        or not isinstance(available, bool)
        or status not in {"healthy", "degraded", "unknown", "unavailable"}
        or not isinstance(diagnostics, list)
        or not all(isinstance(item, dict) for item in diagnostics)
    ):
        return "blocking", [
            {
                "name": "provider_health",
                "status": "blocking",
                "message": "provider health entry does not match the canonical schema",
            }
        ]

    check_status = _doctor_provider_status(
        status,
        available=available,
        platform_ready=platform_ready,
    )
    checks: list[JsonValue] = [
        {
            "name": "provider_health",
            "status": check_status,
            "provider_id": provider_id,
            "provider_type": provider_type,
            "provider_status": status,
            "available": available,
            "diagnostics": diagnostics,
            "error_code": provider.get("error_code"),
            "guidance": (
                provider.get("operator_action")
                if isinstance(provider.get("operator_action"), str)
                else _doctor_guidance(
                    "unavailable" if check_status == "blocking" else status,
                    required=check_status == "blocking",
                )
            ),
        }
    ]
    overall = check_status
    dependencies = provider.get("dependencies", [])
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list):
        checks.append(
            {
                "name": "dependency_health_schema",
                "status": "blocking",
                "provider_id": provider_id,
                "message": "provider dependencies must be a JSON list",
            }
        )
        return "blocking", checks

    for dependency in dependencies:
        dependency_status, dependency_check = _doctor_dependency_health(
            dependency,
            provider_id=provider_id,
        )
        overall = _merge_doctor_status(overall, dependency_status)
        checks.append(dependency_check)
    return overall, checks


def _doctor_dependency_health(
    dependency: JsonValue,
    *,
    provider_id: str,
) -> tuple[str, JsonValue]:
    if not isinstance(dependency, dict):
        return "blocking", {
            "name": "dependency_health_schema",
            "status": "blocking",
            "provider_id": provider_id,
            "message": "dependency health entry must be a JSON object",
        }

    dependency_name = dependency.get("name")
    dependency_state = dependency.get("state")
    required = dependency.get("required")
    if (
        not isinstance(dependency_name, str)
        or not isinstance(dependency_state, str)
        or dependency_state not in _DOCTOR_READINESS_STATES
        or not isinstance(required, bool)
    ):
        return "blocking", {
            "name": "dependency_health_schema",
            "status": "blocking",
            "provider_id": provider_id,
            "message": "dependency health entry does not match the canonical schema",
        }

    status = _doctor_dependency_status(dependency_state, required=required)
    action = dependency.get("operator_action")
    return status, {
        "name": "dependency_health",
        "status": status,
        "provider_id": provider_id,
        "dependency": dependency_name,
        "dependency_state": dependency_state,
        "required": required,
        "error_code": dependency.get("error_code"),
        "attempts": dependency.get("attempts"),
        "retry_count": dependency.get("retry_count"),
        "last_retry_error_code": dependency.get("last_retry_error_code"),
        "probe_duration_seconds": dependency.get("probe_duration_seconds"),
        "degraded_duration_seconds": dependency.get("degraded_duration_seconds"),
        "failure_count": dependency.get("failure_count"),
        "recovery_count": dependency.get("recovery_count"),
        "guidance": (
            action
            if isinstance(action, str)
            else _doctor_guidance(dependency_state, required=required)
        ),
    }


def _doctor_readiness_status(state: str, *, ready: bool) -> str:
    if state == "ready":
        return "healthy"
    if state == "degraded" and ready:
        return "degraded"
    return "blocking"


def _doctor_provider_status(
    status: str,
    *,
    available: bool,
    platform_ready: bool,
) -> str:
    if not available or status == "unavailable":
        return "blocking" if not platform_ready else "degraded"
    if status in {"degraded", "unknown"}:
        return "degraded"
    return "healthy"


def _doctor_dependency_status(state: str, *, required: bool) -> str:
    if state == "ready":
        return "healthy"
    if required and state in {
        "reconciling",
        "unavailable",
        "operator_intervention_required",
        "draining",
    }:
        return "blocking"
    return "degraded"


def _merge_doctor_status(current: str, candidate: str) -> str:
    if _DOCTOR_STATUS_RANK[candidate] > _DOCTOR_STATUS_RANK[current]:
        return candidate
    return current


def _doctor_guidance(state: str, *, required: bool) -> str:
    if state == "operator_intervention_required":
        return (
            "inspect canonical recovery diagnostics and resolve only the reported blocker "
            "through the supported operator command"
        )
    if state == "reconciling":
        return (
            "allow canonical reconciliation to complete; inspect the startup recovery report "
            "if the state does not clear"
        )
    if state == "draining":
        return "allow the current drain/shutdown operation to complete before submitting new work"
    if required or state == "unavailable":
        return (
            "restore the required dependency and rerun platform doctor; do not edit canonical "
            "lifecycle state directly"
        )
    if state in {"degraded", "unknown"}:
        return (
            "restore or disable the optional dependency; unrelated canonical operations "
            "may continue"
        )
    return "no operator action required"
