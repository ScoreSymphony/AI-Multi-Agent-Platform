from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.skillspector.runner import container_command  # noqa: E402


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
