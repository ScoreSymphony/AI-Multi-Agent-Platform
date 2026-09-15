from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Pipelock exited before listening on 127.0.0.1:{port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Pipelock did not listen on 127.0.0.1:{port}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _replace_nested_scalar(
    text: str,
    *,
    section: str,
    key: str,
    value: str,
) -> str:
    lines = text.splitlines(keepends=True)
    in_section = False
    for index, line in enumerate(lines):
        if line.startswith(f"{section}:"):
            in_section = True
            continue
        if in_section and line.strip() and not line.startswith((" ", "\t", "#")):
            break
        if not in_section:
            continue
        stripped = line.lstrip()
        if not stripped.startswith(f"{key}:"):
            continue
        indent = line[: len(line) - len(stripped)]
        lines[index] = f"{indent}{key}: {value}\n"
        return "".join(lines)
    raise AssertionError(f"missing {section}.{key} in generated Pipelock config")


def _receipt_action_id(headers: Path) -> str:
    for line in headers.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition(":")
        if separator and name.lower() == "x-pipelock-receipt":
            action_id = value.strip()
            if action_id:
                return action_id
    raise AssertionError("strict /fetch response did not contain X-Pipelock-Receipt")


@pytest.fixture(scope="session", autouse=True)
def verify_strict_fetch_receipt_chain(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Preserve the pinned candidate's signed-receipt gate for the /fetch path."""

    binary_value = os.getenv("PIPELOCK_730_BIN")
    config_value = os.getenv("PIPELOCK_730_CONFIG")
    if not binary_value or not config_value:
        return

    binary = Path(binary_value)
    source_config = Path(config_value)
    if not binary.is_file() or not source_config.is_file():
        raise AssertionError(
            "pinned Pipelock runtime/config is unavailable for receipt verification"
        )

    root = tmp_path_factory.mktemp("pipelock-strict-fetch")
    home = root / "home"
    recorder = root / "recorder"
    config = root / "strict-fetch.yaml"
    pubkey = root / "signing.pub"
    headers = root / "fetch-headers.txt"
    output = root / "fetch-output.txt"
    log_path = root / "pipelock.log"
    home.mkdir()

    text = source_config.read_text(encoding="utf-8")
    text = _replace_nested_scalar(
        text,
        section="flight_recorder",
        key="require_receipts",
        value="true",
    )
    text = _replace_nested_scalar(
        text,
        section="flight_recorder",
        key="dir",
        value=f'"{recorder}"',
    )
    config.write_text(text, encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(home)
    candidate_dir = binary.parent
    signing = subprocess.run(
        (
            str(binary),
            "signing",
            "pubkey",
            "--config",
            str(config),
            "--out",
            str(pubkey),
        ),
        cwd=candidate_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert signing.returncode == 0, signing.stdout + signing.stderr
    assert pubkey.stat().st_size > 0

    port = _free_loopback_port()
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            (
                str(binary),
                "run",
                "--config",
                str(config),
                "--listen",
                f"127.0.0.1:{port}",
            ),
            cwd=candidate_dir,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_tcp(port, process)
            fetch = subprocess.run(
                (
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "20",
                    "--dump-header",
                    str(headers),
                    "--get",
                    "--data-urlencode",
                    "url=https://example.com/",
                    "--output",
                    str(output),
                    f"http://127.0.0.1:{port}/fetch",
                ),
                capture_output=True,
                text=True,
                timeout=25,
            )
            assert fetch.returncode == 0, fetch.stderr
            assert output.stat().st_size > 0
            action_id = _receipt_action_id(headers)
        finally:
            _stop(process)

    receipt_files = sorted(recorder.glob("*.jsonl"))
    assert receipt_files, "strict /fetch did not produce recorder evidence"
    assert any(
        action_id in path.read_text(encoding="utf-8") for path in receipt_files
    ), "X-Pipelock-Receipt action id was absent from recorder evidence"

    verification = subprocess.run(
        (
            str(binary),
            "verify-receipt",
            "--chain",
            str(recorder),
            "--key",
            str(pubkey),
        ),
        cwd=candidate_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert "CHAIN VALID" in verification.stdout
