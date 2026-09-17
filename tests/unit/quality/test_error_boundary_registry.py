from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "broad_exception_audit.py"


def _run(
    tmp_path: Path,
    source: str,
    entries: list[dict[str, object]],
) -> subprocess.CompletedProcess[str]:
    source_path = tmp_path / "src" / "ai_multi_agent_platform" / "sample.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    registry = tmp_path / "classifications.json"
    registry.write_text(
        json.dumps({"schema_version": 1, "entries": entries}),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository-root",
            str(tmp_path),
            "--root",
            str(source_path),
            "--classification-file",
            str(registry),
            "--format",
            "json",
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _entry(
    *,
    scope: str = "probe",
    exception_form: str = "Exception",
    current_action: str = "return None",
) -> dict[str, object]:
    return {
        "file": "src/ai_multi_agent_platform/sample.py",
        "line": 4,
        "scope": scope,
        "exception_form": exception_form,
        "current_action": current_action,
        "classification": "BOUNDARY",
        "decision": "KEEP + JUSTIFY",
        "rationale": "Reviewed fail-closed boundary with no process-control ownership.",
    }


def test_registry_classifies_exact_handler_signature(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "def probe():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:\n"
        "        return None\n",
        [_entry()],
    )

    assert result.returncode == 0, result.stderr
    findings = json.loads(result.stdout)
    assert findings[0]["severity"] == "allowed"
    assert findings[0]["recommended_classification"] == "boundary catch"
    assert findings[0]["justification"] == "registry:BOUNDARY"


def test_registry_fails_when_current_finding_is_unclassified(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "def probe():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:\n"
        "        return None\n",
        [],
    )

    assert result.returncode == 2
    assert "unclassified production findings=1" in result.stderr


def test_registry_fails_when_review_entry_is_stale(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "def probe():\n"
        "    try:\n"
        "        work()\n"
        "    except OSError:\n"
        "        return None\n",
        [_entry()],
    )

    assert result.returncode == 2
    assert "stale classification entries=1" in result.stderr


def test_registry_cannot_authorize_process_control_risk(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "def probe():\n"
        "    try:\n"
        "        work()\n"
        "    except BaseException:\n"
        "        return None\n",
        [_entry(exception_form="BaseException")],
    )

    assert result.returncode == 2
    assert "cannot authorize cancellation/process-control risk" in result.stderr
