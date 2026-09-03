"""Dependency-free shell completion generation for the platform CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Literal, TextIO, cast

Shell = Literal["bash", "zsh", "fish"]

_GLOBAL_OPTIONS = (
    "-h",
    "--help",
    "--config",
    "--profile",
    "--endpoint",
    "--timeout",
    "--retries",
    "--json",
    "--verbose",
    "--yes",
    "--client-version",
)

_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "profile": ("list", "show", "set", "use"),
    "project": ("list", "show", "create"),
    "workspace": ("list", "show", "create"),
    "task": ("list", "create", "show", "queue", "start", "cancel", "retry", "timeline"),
    "run": ("list", "show", "cancel"),
    "model-provider": ("list", "show", "enable", "disable", "refresh-health"),
    "model": ("list", "show", "enable", "disable"),
    "plan": ("list", "show"),
    "step": ("list", "show"),
    "artifact": ("list", "show"),
    "result": ("list", "show"),
    "extension": ("collections", "commands", "list", "show"),
}

_TOP_LEVEL = ("status", "health", "version", "doctor", *_SUBCOMMANDS)


def completion_script(shell: Shell) -> str:
    """Return a deterministic completion script without importing shell-specific packages."""

    if shell == "bash":
        return _bash_script()
    if shell == "zsh":
        return _zsh_script()
    return _fish_script()


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="platform-completion",
        description="Generate shell completion for the platform CLI",
    )
    parser.add_argument("shell", choices=("bash", "zsh", "fish"))
    args = parser.parse_args(argv)
    shell = cast(Shell, args.shell)
    (stdout or sys.stdout).write(completion_script(shell))
    return 0


def _bash_script() -> str:
    cases = "\n".join(
        f'    {command}) candidates="{" ".join(subcommands)}" ;;'
        for command, subcommands in _SUBCOMMANDS.items()
    )
    return (
        "_platform_complete() {\n"
        "  local cur root candidates\n"
        '  cur="${COMP_WORDS[COMP_CWORD]}"\n'
        '  if [[ "$cur" == -* ]]; then\n'
        f'    COMPREPLY=( $(compgen -W "{" ".join(_GLOBAL_OPTIONS)}" -- "$cur") )\n'
        "    return\n"
        "  fi\n"
        "  if (( COMP_CWORD == 1 )); then\n"
        f'    COMPREPLY=( $(compgen -W "{" ".join(_TOP_LEVEL)}" -- "$cur") )\n'
        "    return\n"
        "  fi\n"
        "  if (( COMP_CWORD != 2 )); then\n"
        "    return\n"
        "  fi\n"
        '  root="${COMP_WORDS[1]}"\n'
        '  candidates=""\n'
        '  case "$root" in\n'
        f"{cases}\n"
        "  esac\n"
        '  COMPREPLY=( $(compgen -W "$candidates" -- "$cur") )\n'
        "}\n"
        "complete -F _platform_complete platform\n"
    )


def _zsh_script() -> str:
    cases = "\n".join(
        f"    {command}) _values 'subcommand' {' '.join(subcommands)} ;;"
        for command, subcommands in _SUBCOMMANDS.items()
    )
    return (
        "#compdef platform\n"
        "_platform() {\n"
        "  local state\n"
        "  _arguments \\\n"
        f"    '1:command:({' '.join(_TOP_LEVEL)})' \\\n"
        "    '2:subcommand:->subcommand' \\\n"
        f"    '*:option:({' '.join(_GLOBAL_OPTIONS)})'\n"
        "  if [[ $state == subcommand ]]; then\n"
        "    case $words[2] in\n"
        f"{cases}\n"
        "    esac\n"
        "  fi\n"
        "}\n"
        "compdef _platform platform\n"
    )


def _fish_script() -> str:
    lines = [
        "complete -c platform -f",
        f"complete -c platform -n '__fish_use_subcommand' -a '{' '.join(_TOP_LEVEL)}'",
    ]
    for command, subcommands in _SUBCOMMANDS.items():
        lines.append(
            "complete -c platform "
            f"-n '__fish_seen_subcommand_from {command}' -a '{' '.join(subcommands)}'"
        )
    for option in _GLOBAL_OPTIONS:
        if option.startswith("--"):
            lines.append(f"complete -c platform -l {option.removeprefix('--')}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
