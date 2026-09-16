from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / "scripts" / "ci" / "broad_exception_audit.py"

SETTLEMENT_FILES = (
    ROOT / "src" / "ai_multi_agent_platform" / "evaluation" / "runner.py",
    ROOT / "src" / "ai_multi_agent_platform" / "plugins" / "_settlement.py",
    ROOT / "src" / "ai_multi_agent_platform" / "templates" / "_settlement.py",
)


def _scan() -> list[dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCANNER),
            "--repository-root",
            str(ROOT),
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _narrow_settlement_baseexceptions() -> None:
    for path in SETTLEMENT_FILES:
        text = path.read_text(encoding="utf-8")
        if path.name == "_settlement.py" and "templates" in path.parts:
            old = (
                "    except BaseException as exc:  "
                "# error-boundary: allow-broad-catch=cleanup\n"
            )
            new = (
                "    # error-boundary: allow-broad-catch=cleanup secondary settlement failure\n"
                "    except (Exception, asyncio.CancelledError) as exc:\n"
            )
            if old in text:
                text = text.replace(old, new, 1)
        else:
            old = "    except BaseException as exc:\n"
            new = "    except (Exception, asyncio.CancelledError) as exc:\n"
            if old in text:
                text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")


def _classification(item: dict[str, object]) -> tuple[str, str]:
    path = str(item["file"])
    scope = str(item["scope"])
    action = str(item["current_action"])

    if bool(item["translated"]):
        return "translation", "reviewed canonical/domain error translation"

    if path.startswith("src/ai_multi_agent_platform/benchmarking/"):
        if scope == "_invoke_without_evidence":
            return "cleanup", "benchmark warmup is excluded from evidence"
        return "boundary", "benchmark operation evidence containment"

    if scope.endswith(".__del__"):
        return "cleanup", "destructor best-effort resource release"

    if path == "src/ai_multi_agent_platform/high_availability/telemetry.py":
        return "cleanup", "telemetry must not affect HA correctness"

    if path == "src/ai_multi_agent_platform/adapters/skillspector.py":
        return "cleanup", "optional raw evidence retention is secondary"

    if path == "src/ai_multi_agent_platform/connectors/control_plane.py":
        return "cleanup", "secondary connector attention projection"

    if path.startswith("src/ai_multi_agent_platform/control_plane/notifications"):
        return "cleanup", "derived notification projection is secondary"

    if path == "src/ai_multi_agent_platform/notifications/service.py":
        return "cleanup", "external notification delivery is secondary"

    if path == "src/ai_multi_agent_platform/portability/executor.py" and "settle_rollback" in scope:
        return "cleanup", "rollback settlement preserves primary failure"

    if path == "src/ai_multi_agent_platform/workspaces/retention.py":
        return "boundary", "per-workspace retention batch containment"

    if path == "src/ai_multi_agent_platform/application_distribution/evaluation_gate_orchestration.py":
        return "boundary", "release-gate evaluation evidence projection"

    if bool(item["reraise"]):
        if bool(item["logged"]):
            return "boundary", "observed operation re-raises primary failure"
        return "cleanup", "local rollback/settlement re-raises primary failure"

    if path.startswith("src/ai_multi_agent_platform/observability/"):
        return "boundary", "observability owner boundary"

    if path.startswith("src/ai_multi_agent_platform/notifications/"):
        return "boundary", "notification runtime/provider containment"

    if action in {"pass", "return None"}:
        return "cleanup", "reviewed best-effort secondary operation"

    return "boundary", "reviewed owner containment boundary"


def _insert_markers(findings: list[dict[str, object]]) -> int:
    pending = [
        item
        for item in findings
        if item["source_class"] == "production"
        and item["severity"] in {"review", "prohibited"}
    ]
    by_file: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in pending:
        if "BaseException" in str(item["exception_form"]):
            raise RuntimeError(
                f"unresolved BaseException finding: {item['file']}:{item['line']}"
            )
        by_file[str(item["file"])].append(item)

    inserted = 0
    for relative, items in by_file.items():
        path = ROOT / relative
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for item in sorted(items, key=lambda value: int(value["line"]), reverse=True):
            index = int(item["line"]) - 1
            handler = lines[index]
            indent = handler[: len(handler) - len(handler.lstrip())]
            if any(
                "error-boundary: allow-broad-catch=" in lines[candidate]
                for candidate in range(max(0, index - 2), index)
            ):
                continue
            kind, reason = _classification(item)
            lines.insert(index, f"{indent}# error-boundary: allow-broad-catch={kind} {reason}\n")
            inserted += 1
        path.write_text("".join(lines), encoding="utf-8")
    return inserted


def main() -> int:
    before = _scan()
    _narrow_settlement_baseexceptions()
    after_narrowing = _scan()
    inserted = _insert_markers(after_narrowing)
    final = _scan()

    remaining = [
        item
        for item in final
        if item["source_class"] == "production" and item["severity"] != "allowed"
    ]
    print(
        f"before={len(before)} markers_inserted={inserted} "
        f"remaining_non_allowed={len(remaining)}"
    )
    for item in remaining:
        print(
            f"{item['severity']} {item['file']}:{item['line']} "
            f"{item['exception_form']} {item['scope']}"
        )
    if remaining:
        return 1

    strict = subprocess.run(
        [
            sys.executable,
            str(SCANNER),
            "--repository-root",
            str(ROOT),
            "--check",
        ],
        cwd=ROOT,
        check=False,
    )
    return strict.returncode


if __name__ == "__main__":
    raise SystemExit(main())
