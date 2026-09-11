from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/issue725_materialize_runtime_assets.py"
PAIRS = (
    (
        Path("schemas/backup-manifest-v1.schema.json"),
        Path("src/ai_multi_agent_platform/backup/backup-manifest-v1.schema.json"),
    ),
    (
        Path("release/compatibility.json"),
        Path("src/ai_multi_agent_platform/release/compatibility.json"),
    ),
)


def run_materializer(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository-root",
            str(root),
            *args,
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def seed_canonical_sources(root: Path) -> None:
    for index, (canonical, _) in enumerate(PAIRS):
        source = root / canonical
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"canonical-{index}\n".encode())


def test_repository_runtime_assets_are_in_sync() -> None:
    result = run_materializer(ROOT, "--check")
    assert result.returncode == 0, result.stderr


def test_materializer_copies_canonical_sources(tmp_path: Path) -> None:
    seed_canonical_sources(tmp_path)

    result = run_materializer(tmp_path)

    assert result.returncode == 0, result.stderr
    for canonical, generated in PAIRS:
        assert (tmp_path / generated).read_bytes() == (tmp_path / canonical).read_bytes()


def test_check_rejects_stale_generated_asset(tmp_path: Path) -> None:
    seed_canonical_sources(tmp_path)
    assert run_materializer(tmp_path).returncode == 0

    generated = tmp_path / PAIRS[0][1]
    generated.write_bytes(b"stale\n")

    result = run_materializer(tmp_path, "--check")

    assert result.returncode == 1
    assert "stale generated runtime asset" in result.stderr


def test_check_rejects_missing_generated_asset(tmp_path: Path) -> None:
    seed_canonical_sources(tmp_path)
    assert run_materializer(tmp_path).returncode == 0

    generated = tmp_path / PAIRS[1][1]
    generated.unlink()

    result = run_materializer(tmp_path, "--check")

    assert result.returncode == 1
    assert "missing generated runtime asset" in result.stderr
