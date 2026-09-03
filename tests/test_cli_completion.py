from __future__ import annotations

from io import StringIO

import pytest

from ai_multi_agent_platform.cli.completion import candidates, main


def test_completion_candidates_cover_root_and_nested_commands() -> None:
    root = candidates([""])
    assert "task" in root
    assert "model-provider" in root
    assert "extension" in root

    task = candidates(["task", ""])
    assert {"create", "list", "show", "cancel", "retry", "timeline"} <= set(task)

    extension = candidates(["extension", ""])
    assert {"collections", "commands", "list", "show"} <= set(extension)


def test_completion_candidates_cover_options_and_choices() -> None:
    task_list_options = candidates(["task", "list", "--d"])
    assert task_list_options == ("--direction",)

    direction = candidates(["task", "list", "--direction", ""])
    assert direction == ("asc", "desc")

    owner_type = candidates(["project", "create", "--owner-type", ""])
    assert owner_type == ("organization", "service", "team", "user")


def test_completion_scripts_are_dependency_free_shell_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for shell, marker in (
        ("bash", "complete -F _platform_complete platform"),
        ("zsh", "compdef _platform_complete platform"),
        ("fish", "complete -c platform"),
    ):
        output = StringIO()
        monkeypatch.setattr("sys.stdout", output)
        assert main([shell]) == 0
        script = output.getvalue()
        assert marker in script
        assert "platform-completion candidates" in script
