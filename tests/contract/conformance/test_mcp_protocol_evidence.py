from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.conformance.mcp_protocol import (
    MCP_PROTOCOL_EVIDENCE_SCHEMA,
    MCPProtocolEvidence,
    MCPProtocolScenarioEvidence,
    combine_mcp_compatibility,
    load_mcp_conformance_pins,
    missing_mcp_protocol_evidence,
    sha256_text,
)

_REPOSITORY_ROOT = Path(__file__).parents[3]
_PIN_MANIFEST = _REPOSITORY_ROOT / "conformance/mcp/pins.json"


def _protocol_evidence(*, conformant: bool = True) -> MCPProtocolEvidence:
    scenario = MCPProtocolScenarioEvidence(
        scenario_id="initialize",
        status="pass" if conformant else "fail",
        exit_code=0 if conformant else 1,
        diagnostics=None if conformant else "wire mismatch",
        stdout_sha256=sha256_text("stdout"),
        stderr_sha256=sha256_text("stderr"),
    )
    return MCPProtocolEvidence(
        schema=MCP_PROTOCOL_EVIDENCE_SCHEMA,
        platform_commit="abc123",
        platform_release="0.0.1",
        adapter_revision="abc123",
        sdk_version="2.1.1",
        protocol_revision="2025-11-25",
        suite_repository="https://github.com/modelcontextprotocol/conformance",
        suite_version="v0.1.16",
        suite_commit="21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0",
        mode="client",
        transport_profile="streamable-http",
        claimed=True,
        gating=True,
        protocol_conformant=conformant,
        timestamp_utc="2026-09-11T00:00:00+00:00",
        environment={"python": "3.12"},
        scenarios=(scenario,),
    )


def test_pin_manifest_separates_stable_claim_from_prerelease_probe() -> None:
    pins = load_mcp_conformance_pins(_PIN_MANIFEST)

    stable = pins.track("stable")
    assert stable.protocol_revision == "2025-11-25"
    assert stable.suite_version == "v0.1.16"
    assert stable.suite_commit == "21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0"
    assert stable.claimed is True
    assert stable.gating is True

    prerelease = pins.track("prerelease")
    assert prerelease.protocol_revision == "2026-07-28"
    assert prerelease.suite_version == "0.2.0-alpha.11"
    assert prerelease.suite_commit == "a983ba93c91e0bb31d0b6849eeb52f0ad1083107"
    assert prerelease.claimed is False
    assert prerelease.gating is False


def test_protocol_evidence_round_trip_is_fail_closed() -> None:
    evidence = _protocol_evidence()
    parsed = MCPProtocolEvidence.from_json(evidence.to_json())

    assert parsed == evidence
    assert parsed.protocol_conformant is True

    payload = json.loads(evidence.to_json())
    payload["protocol_conformant"] = False
    with pytest.raises(ValueError, match="does not match scenario results"):
        MCPProtocolEvidence.from_json(json.dumps(payload))


def test_protocol_and_platform_conformance_are_independent_dimensions() -> None:
    protocol_pass_platform_fail = combine_mcp_compatibility(
        _protocol_evidence(conformant=True),
        platform_conformant=False,
    )
    assert protocol_pass_platform_fail.protocol_conformant is True
    assert protocol_pass_platform_fail.platform_conformant is False
    assert protocol_pass_platform_fail.compatible is False

    protocol_fail_platform_pass = combine_mcp_compatibility(
        _protocol_evidence(conformant=False),
        platform_conformant=True,
    )
    assert protocol_fail_platform_pass.protocol_conformant is False
    assert protocol_fail_platform_pass.platform_conformant is True
    assert protocol_fail_platform_pass.compatible is False


def test_missing_protocol_evidence_never_becomes_compatibility_claim() -> None:
    compatibility = missing_mcp_protocol_evidence(platform_conformant=True)

    assert compatibility.protocol_evidence_status == "missing"
    assert compatibility.protocol_conformant is None
    assert compatibility.platform_conformant is True
    assert compatibility.claimed is False
    assert compatibility.compatible is False


def test_runner_rejects_unpinned_suite_version_before_execution(tmp_path: Path) -> None:
    suite_root = tmp_path / "suite"
    suite_root.mkdir()
    (suite_root / "package.json").write_text(
        json.dumps({"version": "999.0.0"}),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"

    completed = subprocess.run(
        (
            sys.executable,
            "scripts/ci/mcp_protocol_conformance.py",
            "--track",
            "stable",
            "--suite-root",
            str(suite_root),
            "--json-report",
            str(report),
        ),
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "suite version drift" in completed.stderr
    assert not report.exists()
