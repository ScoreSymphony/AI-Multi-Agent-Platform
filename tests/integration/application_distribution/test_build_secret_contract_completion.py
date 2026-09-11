from __future__ import annotations

import pytest

from ai_multi_agent_platform.application_distribution import (
    BuildSpecification,
    BuildTarget,
    PackageType,
    control_plane,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    Telemetry,
    TelemetryContext,
    TelemetrySeverity,
)

_SECRET_VALUE = "fixture-secret-value-748-contract-completion"


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


def test_legacy_secret_references_accept_only_opaque_reference_ids() -> None:
    with pytest.raises(ValueError, match="opaque secret_ref_\\* identifiers"):
        BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            secret_references=(_SECRET_VALUE,),
        )

    specification = BuildSpecification(
        command=("tool", "build"),
        targets=(_target(),),
        secret_references=("secret_ref_signing_key",),
    )

    assert specification.secret_references == ("secret_ref_signing_key",)


def test_control_plane_rejects_plaintext_in_legacy_secret_references() -> None:
    with pytest.raises(ContractError) as raised:
        control_plane._build_spec(  # noqa: SLF001 - exact parser-boundary regression
            {
                "command": ["tool", "build"],
                "targets": [
                    {
                        "target_id": "local-test",
                        "os_name": "test",
                        "architecture": "test",
                        "package_type": "archive",
                        "output_path": "dist/app.bin",
                    }
                ],
                "secret_references": [_SECRET_VALUE],
            },
            default_spec_id=new_id("build_spec"),
        )

    assert raised.value.code is ErrorCode.INVALID_REQUEST
    assert _SECRET_VALUE not in raised.value.message


def test_structured_telemetry_redacts_secret_backed_build_attributes() -> None:
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    context = TelemetryContext(correlation_id="issue-748-secret-telemetry")
    attributes = {
        "PRIVATE_INDEX_TOKEN": _SECRET_VALUE,
        "nested": {"access_token": _SECRET_VALUE},
        "safe": "visible",
    }

    telemetry.log(
        severity=TelemetrySeverity.INFO,
        component=FailureComponent.EXECUTION,
        event_name="application_build.secret_probe",
        context=context,
        attributes=attributes,
    )
    telemetry.metric(
        "application_build.secret_probe",
        1.0,
        context=context,
        attributes=attributes,
    )
    telemetry.timeline(
        event_name="application_build.secret_probe",
        component=FailureComponent.EXECUTION,
        context=context,
        attributes=attributes,
    )
    span = telemetry.start_span("application_build.secret_probe", context=context)
    telemetry.finish_span(span, attributes=attributes)

    serialized = repr((exporter.logs, exporter.metrics, exporter.timeline, exporter.spans))
    assert _SECRET_VALUE not in serialized
    assert "[REDACTED]" in serialized
    assert "visible" in serialized
