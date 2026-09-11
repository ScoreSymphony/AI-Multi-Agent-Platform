import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.skillspector.runner import (  # noqa: E402
    container_command,
    prepare_output_directory,
)


def test_container_command_uses_explicit_entrypoint_without_duplicate_binary(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    image = "skillspector-eval:2.11.2"

    command = container_command("docker", image, input_dir, output_dir)
    image_index = command.index(image)

    assert command[image_index - 2 : image_index] == ["--entrypoint", "skillspector"]
    assert command[image_index + 1] == "scan"
    assert command[image_index + 1 :].count("skillspector") == 0
    assert ["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"] == command[
        command.index("--tmpfs") : command.index("--tmpfs") + 2
    ]
    assert ["-e", "HOME=/tmp"] == command[command.index("-e") : command.index("-e") + 2]


def test_output_bind_target_is_writable_without_dac_override(tmp_path: Path) -> None:
    output_dir = tmp_path / "private-parent" / "output"
    output_dir.parent.mkdir(mode=0o700)

    prepare_output_directory(output_dir)

    assert output_dir.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(output_dir.stat().st_mode) == 0o733
