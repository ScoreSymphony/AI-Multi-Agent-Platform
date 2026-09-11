#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DistributionAsset:
    canonical: Path
    wheel_member: str
    sdist_suffix: str


ASSETS = (
    DistributionAsset(
        Path("schemas/backup-manifest-v1.schema.json"),
        "ai_multi_agent_platform/backup/backup-manifest-v1.schema.json",
        "src/ai_multi_agent_platform/backup/backup-manifest-v1.schema.json",
    ),
    DistributionAsset(
        Path("release/compatibility.json"),
        "ai_multi_agent_platform/release/compatibility.json",
        "src/ai_multi_agent_platform/release/compatibility.json",
    ),
)


def default_repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def single_artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label} in {dist_dir}, found {len(matches)}")
    return matches[0]


def expected_bytes(root: Path, asset: DistributionAsset) -> bytes:
    source = root / asset.canonical
    if not source.is_file():
        raise RuntimeError(f"missing canonical runtime asset: {asset.canonical}")
    return source.read_bytes()


def verify_wheel(path: Path, root: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for asset in ASSETS:
            if asset.wheel_member not in names:
                raise RuntimeError(f"{path.name} is missing runtime asset {asset.wheel_member}")
            if archive.read(asset.wheel_member) != expected_bytes(root, asset):
                raise RuntimeError(f"{path.name} contains stale runtime asset {asset.wheel_member}")


def verify_sdist(path: Path, root: Path) -> None:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        for asset in ASSETS:
            matches = [
                member
                for member in members
                if member.isfile() and member.name.endswith(asset.sdist_suffix)
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"{path.name} must contain exactly one runtime asset ending in "
                    f"{asset.sdist_suffix}; found {len(matches)}"
                )
            payload = archive.extractfile(matches[0])
            if payload is None:
                raise RuntimeError(
                    f"unable to read runtime asset {matches[0].name} from {path.name}"
                )
            if payload.read() != expected_bytes(root, asset):
                raise RuntimeError(f"{path.name} contains stale runtime asset {matches[0].name}")


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def verify_installed_wheel(path: Path, root: Path) -> None:
    smoke_script = root / "scripts/ci/issue725_installed_resource_smoke.py"
    if not smoke_script.is_file():
        raise RuntimeError(f"missing installed-resource smoke script: {smoke_script}")

    with tempfile.TemporaryDirectory(prefix="issue725-wheel-smoke-") as temporary:
        temporary_root = Path(temporary)
        venv_dir = temporary_root / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = venv_python(venv_dir)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                str(path),
            ],
            cwd=temporary_root,
            check=True,
        )
        subprocess.run(
            [str(python), str(smoke_script)],
            cwd=temporary_root,
            check=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify canonical runtime assets inside built distributions and an installed wheel."
    )
    parser.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist"))
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=default_repository_root(),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repository_root.resolve()
    dist_dir = args.dist_dir.resolve()

    try:
        wheel = single_artifact(dist_dir, "*.whl", "wheel")
        sdist = single_artifact(dist_dir, "*.tar.gz", "sdist")
        verify_wheel(wheel, root)
        verify_sdist(sdist, root)
        verify_installed_wheel(wheel, root)
    except (
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Built distributions and installed wheel contain canonical runtime assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
