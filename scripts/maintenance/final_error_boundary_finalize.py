from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    scanner = ROOT / "scripts" / "ci" / "broad_exception_audit.py"
    _replace(
        scanner,
        'help="Exit 1 only when clearly prohibited broad-catch shapes are present.",',
        'help="Exit 1 when any production broad catch is prohibited or unclassified.",',
    )
    _replace(
        scanner,
        '    return int(args.check and any(item.severity == "prohibited" for item in findings))\n',
        '    return int(\n'
        '        args.check\n'
        '        and any(\n'
        '            item.source_class == "production" and item.severity != "allowed"\n'
        '            for item in findings\n'
        '        )\n'
        '    )\n',
    )

    tests = ROOT / "tests" / "unit" / "quality" / "test_broad_exception_audit.py"
    marker = "\ndef test_development_tools_are_classified_separately(tmp_path: Path) -> None:\n"
    addition = '''\n\ndef test_check_rejects_unclassified_production_catch(tmp_path: Path) -> None:\n    code, findings, _ = _scan(\n        tmp_path,\n        "def boundary():\\n"\n        "    try:\\n"\n        "        work()\\n"\n        "    except Exception as exc:\\n"\n        "        record(type(exc).__name__)\\n",\n        check=True,\n    )\n    assert code == 1\n    assert findings[0]["recommended_classification"] == "needs review"\n    assert findings[0]["severity"] == "review"\n'''
    text = tests.read_text(encoding="utf-8")
    if marker not in text:
        raise RuntimeError("test insertion marker not found")
    tests.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
