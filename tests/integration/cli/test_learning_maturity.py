from __future__ import annotations

import argparse

from ai_multi_agent_platform.cli.learning import add_learning_parser


def test_learning_help_marks_surface_experimental() -> None:
    parser = argparse.ArgumentParser(prog="platform")
    areas = parser.add_subparsers(dest="area", required=True)
    add_learning_parser(areas)

    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    help_text = subparsers.choices["learning"].format_help()

    assert "Experimental" in help_text
