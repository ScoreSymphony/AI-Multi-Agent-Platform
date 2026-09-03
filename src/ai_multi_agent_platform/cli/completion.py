"""Dependency-free shell completion for the canonical platform CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .main import _build_parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="platform-completion",
        description="Generate or serve shell completion for the platform CLI",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for shell in ("bash", "zsh", "fish"):
        commands.add_parser(shell, help=f"print {shell} completion setup")
    candidate_parser = commands.add_parser("candidates", help=argparse.SUPPRESS)
    candidate_parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == "bash":
        print(_bash_script())
        return 0
    if args.command == "zsh":
        print(_zsh_script())
        return 0
    if args.command == "fish":
        print(_fish_script())
        return 0
    if args.command == "candidates":
        for item in candidates(args.words):
            print(item)
        return 0
    parser.error(f"unsupported completion command: {args.command}")
    return 2


def candidates(words: Sequence[str]) -> tuple[str, ...]:
    """Return command/option candidates for words after the ``platform`` executable."""

    parser = _build_parser()
    current = words[-1] if words else ""
    completed = list(words[:-1]) if words else []
    pending_action: argparse.Action | None = None
    index = 0

    while index < len(completed):
        token = completed[index]
        subcommands = _subcommands(parser)
        if token in subcommands:
            parser = subcommands[token]
            pending_action = None
            index += 1
            continue

        if token.startswith("-"):
            option_name, has_inline_value = _option_name(token)
            action = _option_action(parser, option_name)
            if action is None:
                index += 1
                continue
            pending_action = None
            if has_inline_value or action.nargs == 0:
                index += 1
                continue
            if index + 1 >= len(completed):
                pending_action = action
                index += 1
                continue
            index += 2
            continue

        pending_action = None
        index += 1

    if pending_action is not None and pending_action.choices is not None:
        return _matching((str(choice) for choice in pending_action.choices), current)

    if current.startswith("-"):
        return _matching(_option_strings(parser), current)

    subcommands = _subcommands(parser)
    if subcommands:
        return _matching(subcommands, current)
    return ()


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _option_strings(parser: argparse.ArgumentParser) -> tuple[str, ...]:
    values: set[str] = set()
    for action in parser._actions:
        values.update(action.option_strings)
    return tuple(sorted(values))


def _option_action(parser: argparse.ArgumentParser, option: str) -> argparse.Action | None:
    for action in parser._actions:
        if option in action.option_strings:
            return action
    return None


def _option_name(token: str) -> tuple[str, bool]:
    if token.startswith("--") and "=" in token:
        return token.split("=", 1)[0], True
    return token, False


def _matching(values: Sequence[str] | dict[str, argparse.ArgumentParser] | object, prefix: str) -> tuple[str, ...]:
    if isinstance(values, dict):
        items = values.keys()
    else:
        try:
            items = iter(values)  # type: ignore[arg-type]
        except TypeError:
            return ()
    return tuple(sorted(str(value) for value in items if str(value).startswith(prefix)))


def _bash_script() -> str:
    return r'''_platform_complete() {
    local IFS=$'\n'
    COMPREPLY=( $(platform-completion candidates "${COMP_WORDS[@]:1}") )
}
complete -F _platform_complete platform'''


def _zsh_script() -> str:
    return r'''_platform_complete() {
    local -a replies
    replies=("${(@f)$(platform-completion candidates "${words[@]:2}")}")
    compadd -- $replies
}
compdef _platform_complete platform'''


def _fish_script() -> str:
    return r'''function __platform_complete
    set -l tokens (commandline -opc)
    if test (count $tokens) -gt 0
        set -e tokens[1]
    end
    platform-completion candidates $tokens (commandline -ct)
end
complete -c platform -f -a '(__platform_complete)' '''.rstrip()


if __name__ == "__main__":
    raise SystemExit(main())
