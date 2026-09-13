from __future__ import annotations

import argparse

import pytest

from ai_multi_agent_platform.cli.learning import add_learning_parser


def test_learning_help_marks_surface_experimental(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser(prog="platform")
    areas = parser.add_subparsers(dest="area", required=True)
    add_learning_parser(areas)

    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["learning", "--help"])

    assert exit_info.value.code == 0
    assert "Experimental" in capsys.readouterr().out
