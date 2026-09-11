"""Subprocess/container harness for the SkillSpector #800 evaluation.

The default path requires a container runtime and disables networking. Direct
host execution is opt-in because a subprocess alone is not a security boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence

PINNED_VERSION = "2.11.2"
PINNED_REVISION = "69dcdfb74487d361ba4c811d088cfdea2ff3a9dc"
DEFAULT_IMAGE = f"skillspector-eval:{PINNED_VERSION}"
MAX_CAPTURE_BYTES = 256_000
SAFE_ENV_KEYS = {"PATH", "LANG", "LC_ALL", "TMPDIR"}


def digest_tree(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def reject_symlinks(root: Path) -> None:
    """Reject candidate symlinks so staging cannot escape the candidate tree."""
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"evaluation source contains unsupported symlink: {path}")


def sanitized_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}


def container_command(runtime: str, image: str, input_dir: Path, output_dir: Path) -> list[str]:
    """Build the isolated static-scan command for the pinned upstream image.

    The upstream SkillSpector Dockerfile already declares ``ENTRYPOINT [\"skillspector\"]``.
    We nevertheless override it explicitly so the harness is deterministic across a locally
    rebuilt image and any future evaluation image. Only CLI arguments follow the image name;
    this avoids accidentally executing ``skillspector skillspector scan ...``.
    """
    return [
        runtime,
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--memory=1g",
        "--cpus=1.0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{input_dir}:/scan:ro",
        "-v",
        f"{output_dir}:/out:rw",
        "--entrypoint",
        "skillspector",
        image,
        "scan",
        "/scan",
        "--no-llm",
        "--format",
        "json",
        "--output",
        "/out/report.json",
    ]


def local_command(input_dir: Path, output_dir: Path) -> list[str]:
    return [
        "skillspector",
        "scan",
        str(input_dir),
        "--no-llm",
        "--format",
        "json",
        "--output",
        str(output_dir / "report.json"),
    ]


def run_command(
    command: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=sanitized_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"SkillSpector timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise RuntimeError(f"SkillSpector process could not start: {exc}") from exc
    if len(completed.stdout.encode()) > MAX_CAPTURE_BYTES:
        completed.stdout = completed.stdout.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    if len(completed.stderr.encode()) > MAX_CAPTURE_BYTES:
        completed.stderr = completed.stderr.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    return completed


def scan_result_is_usable(returncode: int, report: object) -> bool:
    """Return whether the scanner produced a structurally usable evidence report.

    SkillSpector v2.11.2 uses exit code 1 for policy-relevant risk findings, so
    return code 1 is not itself a scanner failure. Exit code 2 and reports whose
    `execution_successful` flag is not explicitly true are fail-closed.
    """
    return (
        returncode in {0, 1}
        and isinstance(report, Mapping)
        and report.get("execution_successful") is True
    )


def evaluate(
    source: Path,
    *,
    runtime: str | None,
    image: str,
    allow_local_process: bool,
    timeout_seconds: int,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("evaluation source must be a directory")
    reject_symlinks(source)

    with tempfile.TemporaryDirectory(prefix="skillspector-800-") as temp:
        root = Path(temp)
        staged = root / "candidate"
        output = root / "output"
        shutil.copytree(source, staged, symlinks=False)
        output.mkdir()
        candidate_digest = digest_tree(staged)

        selected_runtime = runtime
        if selected_runtime is None:
            selected_runtime = shutil.which("docker") or shutil.which("podman")
        if selected_runtime:
            command = container_command(selected_runtime, image, staged, output)
            isolation = "container_network_none"
        elif allow_local_process:
            if shutil.which("skillspector") is None:
                raise RuntimeError("skillspector executable not found")
            command = local_command(staged, output)
            isolation = "local_process_not_security_boundary"
        else:
            raise RuntimeError(
                "no container sandbox available; refusing host execution without "
                "--allow-local-process"
            )

        completed = run_command(command, timeout_seconds=timeout_seconds)
        report_path = output / "report.json"
        report: object = None
        raw_report_text: str | None = None
        raw_report_sha256: str | None = None
        raw_report_size_bytes: int | None = None
        if report_path.exists():
            raw_report_bytes = report_path.read_bytes()
            raw_report_sha256 = sha256(raw_report_bytes).hexdigest()
            raw_report_size_bytes = len(raw_report_bytes)
            try:
                raw_report_text = raw_report_bytes.decode("utf-8")
                report = json.loads(raw_report_text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                report = None

        process_ok = scan_result_is_usable(completed.returncode, report)
        return {
            "candidate_digest": candidate_digest,
            "isolation": isolation,
            "command": command,
            "returncode": completed.returncode,
            "process_ok": process_ok,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "report": report,
            "raw_report_text": raw_report_text,
            "raw_report_sha256": raw_report_sha256,
            "raw_report_size_bytes": raw_report_size_bytes,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--runtime", choices=("docker", "podman"))
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--allow-local-process", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    result = evaluate(
        args.source,
        runtime=args.runtime,
        image=args.image,
        allow_local_process=args.allow_local_process,
        timeout_seconds=args.timeout,
    )
    printable = dict(result)
    printable.pop("raw_report_text", None)
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0 if result["process_ok"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
