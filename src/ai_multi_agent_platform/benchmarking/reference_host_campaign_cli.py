"""CLI for running one reproducible reference-host benchmark campaign."""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import cast

from .reference_host_campaign import (
    ReferenceHostCampaignRunner,
    reference_host_campaign_profile,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-reference-host-campaign")
    parser.add_argument("--profile", choices=("release", "smoke"), required=True)
    parser.add_argument(
        "--host-label",
        required=True,
        help="stable human-readable host label; avoid secrets or public evidence-sensitive values",
    )
    parser.add_argument("--platform-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--work-dir",
        type=Path,
        help=(
            "fresh benchmark state directory on the storage being measured; "
            "required for release profile"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    profile_name = cast(str, args.profile)
    output_dir = cast(Path, args.output_dir)
    work_dir = cast(Path | None, args.work_dir)

    if profile_name == "release" and work_dir is None:
        print(
            "reference-host campaign failed: --work-dir is required for release profile",
            file=sys.stderr,
        )
        return 2

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-reference-host-campaign-")
        work_dir = Path(temporary.name)
        work_dir_mode = "temporary"
    else:
        work_dir_mode = "explicit"

    try:
        runner = ReferenceHostCampaignRunner(
            output_dir=output_dir,
            work_dir=work_dir,
            host_label=cast(str, args.host_label),
            platform_commit=cast(str, args.platform_commit),
            work_dir_mode=work_dir_mode,
        )
        asyncio.run(runner.run(reference_host_campaign_profile(profile_name)))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"reference-host campaign failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
