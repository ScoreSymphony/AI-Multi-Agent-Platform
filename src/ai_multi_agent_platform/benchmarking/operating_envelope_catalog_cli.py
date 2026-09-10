"""CLI for cataloging independently tested host operating envelopes."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .operating_envelope_catalog import OperatingEnvelopeCatalogBuilder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-operating-envelope-catalog")
    parser.add_argument(
        "--host",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="labeled operating-envelope JSON; repeat for each reference host",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = cast(Path, args.output)
    try:
        host_specs = tuple(_parse_host_spec(value) for value in cast(list[str], args.host))
        labels = tuple(label for label, _ in host_specs)
        paths = tuple(path for _, path in host_specs)
        reports = tuple(_load_json_object(path) for path in paths)
        report = OperatingEnvelopeCatalogBuilder().build(
            envelopes=reports,
            labels=labels,
            sources=tuple(str(path) for path in paths),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"operating-envelope catalog failed: {exc}", file=sys.stderr)
        return 2


def _parse_host_spec(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label.strip() or not raw_path.strip():
        raise ValueError("--host must use non-empty LABEL=PATH syntax")
    return label.strip(), Path(raw_path.strip())


def _load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: report must be a JSON object")
    return cast(Mapping[str, Any], payload)


if __name__ == "__main__":
    raise SystemExit(main())
