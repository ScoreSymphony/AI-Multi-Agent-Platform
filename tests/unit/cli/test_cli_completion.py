from __future__ import annotations

from io import StringIO

import pytest

from ai_multi_agent_platform.cli.app import build_composed_parser, run_cli
from ai_multi_agent_platform.cli.completion import _subcommands, candidates, main


def test_completion_candidates_cover_root_and_nested_commands() -> None:
    root = candidates([""])
    assert {
        "task",
        "model-provider",
        "extension",
        "auth",
        "approval",
        "repository",
        "registry",
        "learning",
        "trace",
    } <= set(root)

    assert {"login", "session", "credential", "token"} <= set(candidates(["auth", ""]))
    assert {"list", "show", "approve", "deny"} <= set(candidates(["approval", ""]))
    assert {"list", "show", "branches", "commit"} <= set(candidates(["repository", ""]))
    assert {"list", "show", "preview", "activate", "pin", "unpin"} <= set(
        candidates(["registry", ""])
    )
    assert {"candidate", "feedback", "propose", "promote"} <= set(candidates(["learning", ""]))
    assert candidates(["trace", "task_example", "--n"]) == ("--node",)

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


def test_composed_product_surface_is_identical_for_root_help_and_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_composed_parser()
    surface = set(_subcommands(parser))
    expected_wrappers = {"auth", "approval", "repository", "registry", "learning", "trace"}

    assert expected_wrappers <= surface
    assert set(candidates([""])) == surface

    with pytest.raises(SystemExit) as exc_info:
        run_cli(["--help"])
    assert exc_info.value.code == 0

    help_output = capsys.readouterr().out
    for command in sorted(surface):
        assert command in help_output


@pytest.mark.parametrize(
    ("arguments", "marker"),
    [
        (["auth", "--help"], "login"),
        (["approval", "--help"], "approve"),
        (["repository", "--help"], "branch-create"),
        (["registry", "--help"], "activate"),
        (["learning", "--help"], "promote"),
        (["trace", "--help"], "--node"),
    ],
)
def test_nested_help_resolves_to_owning_domain_parser(
    arguments: list[str],
    marker: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_cli(arguments)
    assert exc_info.value.code == 0
    assert marker in capsys.readouterr().out

