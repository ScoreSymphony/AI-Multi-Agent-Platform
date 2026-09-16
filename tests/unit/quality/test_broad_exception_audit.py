from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "broad_exception_audit.py"


def _scan(
    tmp_path: Path,
    source: str,
    *,
    check: bool = False,
    relative_path: str = "src/ai_multi_agent_platform/sample.py",
) -> tuple[int, list[dict[str, object]], str]:
    package = tmp_path / relative_path
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(source, encoding="utf-8")
    command = [
        sys.executable,
        str(SCRIPT),
        "--repository-root",
        str(tmp_path),
        "--root",
        str(package),
        "--format",
        "json",
    ]
    if check:
        command.append("--check")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout), result.stdout


def test_silent_exception_return_none_is_prohibited(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "def load():\n"
        "    try:\n"
        "        return work()\n"
        "    except Exception:\n"
        "        return None\n",
        check=True,
    )
    assert code == 1
    assert len(findings) == 1
    finding = findings[0]
    assert finding["exception_form"] == "Exception"
    assert finding["recommended_classification"] == "unexpected swallowing"
    assert finding["severity"] == "prohibited"


def test_base_exception_cleanup_with_unconditional_reraise_is_allowed(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "def release():\n"
        "    try:\n"
        "        return work()\n"
        "    except BaseException:\n"
        "        capacity.release()\n"
        "        raise\n",
        check=True,
    )
    assert code == 0
    finding = findings[0]
    assert finding["reraise"] is True
    assert finding["cancellation_risk"] is False
    assert finding["recommended_classification"] == "cleanup / best effort"
    assert finding["severity"] == "allowed"


def test_conditional_cancel_reraise_does_not_justify_base_exception(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "def pump():\n"
        "    try:\n"
        "        work()\n"
        "    except BaseException as exc:\n"
        "        if is_cancelled(exc):\n"
        "            raise\n"
        "        queue(exc)\n",
        check=True,
    )
    assert code == 1
    finding = findings[0]
    assert finding["reraise"] is False
    assert finding["cancellation_risk"] is True
    assert finding["shutdown_risk"] is True
    assert finding["severity"] == "prohibited"


def test_explicit_boundary_justification_allows_reviewed_catch(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "def boundary():\n"
        "    try:\n"
        "        provider_call()\n"
        "    # error-boundary: allow-broad-catch=boundary provider SDK outer boundary\n"
        "    except Exception as exc:\n"
        "        translate(exc)\n",
        check=True,
    )
    assert code == 0
    finding = findings[0]
    assert finding["justification"] == "boundary"
    assert finding["recommended_classification"] == "boundary catch"
    assert finding["severity"] == "allowed"


def test_review_marker_cannot_override_process_signal_protection(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "from contextlib import suppress\n"
        "def base_boundary():\n"
        "    try:\n"
        "        work()\n"
        "    # error-boundary: allow-broad-catch=boundary reviewed outer boundary\n"
        "    except BaseException:\n"
        "        pass\n"
        "def bare_boundary():\n"
        "    try:\n"
        "        work()\n"
        "    # error-boundary: allow-broad-catch=cleanup reviewed cleanup\n"
        "    except:\n"
        "        pass\n"
        "def suppressed():\n"
        "    # error-boundary: allow-broad-catch=cleanup reviewed cleanup\n"
        "    with suppress(BaseException):\n"
        "        work()\n",
        check=True,
    )
    assert code == 1
    assert {finding["exception_form"] for finding in findings} == {
        "BaseException",
        "bare except",
        "suppress(BaseException)",
    }
    assert all(finding["severity"] == "prohibited" for finding in findings)
    assert all(finding["cancellation_risk"] is True for finding in findings)
    assert all(finding["shutdown_risk"] is True for finding in findings)
    assert {finding["justification"] for finding in findings} == {"boundary", "cleanup"}


def test_bare_except_and_broad_suppress_are_prohibited(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "from contextlib import suppress\n"
        "def cleanup():\n"
        "    try:\n"
        "        work()\n"
        "    except:\n"
        "        pass\n"
        "    with suppress(Exception):\n"
        "        cleanup_work()\n",
        check=True,
    )
    assert code == 1
    assert {finding["exception_form"] for finding in findings} == {
        "bare except",
        "suppress(Exception)",
    }
    assert all(finding["severity"] == "prohibited" for finding in findings)


def test_specific_suppress_is_inventory_only_and_allowed(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "from contextlib import suppress\n"
        "def cleanup():\n"
        "    with suppress(OSError):\n"
        "        cleanup_work()\n",
        check=True,
    )
    assert code == 0
    assert findings[0]["exception_form"] == "suppress(OSError)"
    assert findings[0]["recommended_classification"] == "cleanup / best effort"
    assert findings[0]["severity"] == "allowed"


def test_exception_text_is_flagged_without_echoing_source_text(tmp_path: Path) -> None:
    marker = "provider-private-text-must-not-cross-boundary"
    _, findings, stdout = _scan(
        tmp_path,
        "def boundary():\n"
        "    try:\n"
        f"        raise RuntimeError('{marker}')\n"
        "    except Exception as exc:\n"
        "        return str(exc)\n",
    )
    assert findings[0]["diagnostic_text_risk"] is True
    assert marker not in stdout


def test_worker_failure_can_be_suppressed_when_outer_cancellation_is_authoritative(
    tmp_path: Path,
) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "import asyncio\n"
        "def settle():\n"
        "    try:\n"
        "        work()\n"
        "    except asyncio.CancelledError:\n"
        "        try:\n"
        "            worker.result()\n"
        "        except Exception:\n"
        "            pass\n"
        "        raise\n",
        check=True,
    )
    assert code == 0
    assert len(findings) == 1
    finding = findings[0]
    assert finding["recommended_classification"] == "cleanup / best effort"
    assert finding["severity"] == "allowed"


def test_development_tools_are_classified_separately(tmp_path: Path) -> None:
    code, findings, _ = _scan(
        tmp_path,
        "def probe():\n    try:\n        work()\n    except Exception:\n        return None\n",
        check=True,
        relative_path="scripts/maintenance/probe.py",
    )
    assert code == 0
    assert findings[0]["recommended_classification"] == "development tool"
    assert findings[0]["severity"] == "review"


def test_dynamic_exception_expression_is_not_echoed_into_audit_output(tmp_path: Path) -> None:
    marker = "not-for-audit-output"
    _, findings, stdout = _scan(
        tmp_path,
        "def boundary():\n"
        "    try:\n"
        "        work()\n"
        f"    except factory('{marker}'):\n"
        "        pass\n",
    )
    assert findings == []
    assert marker not in stdout
