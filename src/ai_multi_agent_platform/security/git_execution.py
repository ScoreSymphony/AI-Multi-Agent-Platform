"""Hermetic Git subprocess controls shared by platform-owned execution paths."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

_DANGEROUS_GIT_ENV_EXACT = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ASKPASS",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EDITOR",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PROXY_COMMAND",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_TEMPLATE_DIR",
        "GIT_TERMINAL_PROMPT",
        "GIT_WORK_TREE",
        "SSH_ASKPASS",
    }
)
_DANGEROUS_GIT_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_DANGEROUS_CONFIG_EXACT = frozenset(
    {
        "commit.gpgsign",
        "core.fsmonitor",
        "core.gitproxy",
        "core.hookspath",
        "core.sshcommand",
        "credential.helper",
        "gpg.program",
        "gpg.ssh.program",
        "protocol.ext.allow",
        "tag.gpgsign",
    }
)
_REMOTE_HELPER = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")
_ALLOWED_URL_SCHEMES = frozenset({"file", "git", "http", "https", "ssh"})


def controlled_git_environment(
    inherited: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
) -> dict[str, str]:
    """Return an inherited environment with Git execution-indirection removed.

    The caller still decides which Git features are supported. This helper only prevents the
    parent process from injecting repository routing, config scopes, hooks/helpers or executable
    overrides through environment variables.
    """

    source = os.environ if inherited is None else inherited
    environment = {str(key): str(value) for key, value in source.items()}
    for key in tuple(environment):
        normalized = key.upper()
        if normalized in _DANGEROUS_GIT_ENV_EXACT or any(
            normalized.startswith(prefix) for prefix in _DANGEROUS_GIT_ENV_PREFIXES
        ):
            environment.pop(key, None)

    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_PAGER": "cat",
        }
    )
    if home is not None:
        resolved_home = Path(home).expanduser().resolve()
        environment["HOME"] = str(resolved_home)
        environment["XDG_CONFIG_HOME"] = str(resolved_home / ".config")
    return environment


def resolve_git_executable(binary: str) -> str:
    """Resolve a Git executable once outside the child environment when possible."""

    candidate = Path(binary).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        return str(candidate)
    resolved = shutil.which(binary)
    return resolved or binary


def unsafe_local_git_config_keys(keys: Iterable[str]) -> tuple[str, ...]:
    """Return local config keys that can redirect or execute code in supported operations."""

    unsafe: set[str] = set()
    for key in keys:
        normalized = key.strip().lower()
        if not normalized:
            continue
        if normalized in _DANGEROUS_CONFIG_EXACT:
            unsafe.add(normalized)
            continue
        if normalized == "include.path" or (
            normalized.startswith("includeif.") and normalized.endswith(".path")
        ):
            unsafe.add(normalized)
            continue
        if normalized.startswith("filter.") and normalized.endswith(
            (".clean", ".smudge", ".process")
        ):
            unsafe.add(normalized)
            continue
        if normalized.startswith("diff.") and normalized.endswith((".command", ".textconv")):
            unsafe.add(normalized)
            continue
        if normalized.startswith("credential.") and normalized.endswith(".helper"):
            unsafe.add(normalized)
            continue
        if normalized.startswith("url.") and normalized.endswith(
            (".insteadof", ".pushinsteadof")
        ):
            unsafe.add(normalized)
            continue
        if normalized.startswith("remote.") and normalized.endswith(
            (".proxy", ".receivepack", ".uploadpack")
        ):
            unsafe.add(normalized)
            continue
        if normalized.startswith("submodule.") and normalized.endswith(".update"):
            unsafe.add(normalized)
    return tuple(sorted(unsafe))


def validate_git_remote_url(url: str) -> str:
    """Reject Git remote-helper execution while preserving built-in transports and local paths."""

    value = url.strip()
    if not value:
        raise ValueError("Git remote URL must not be blank")
    lowered = value.lower()
    if lowered.startswith("ext::") or _REMOTE_HELPER.match(value):
        raise ValueError("Git external remote-helper syntax is not supported")
    if "://" in value:
        scheme = value.split("://", 1)[0].lower()
        if scheme not in _ALLOWED_URL_SCHEMES:
            raise ValueError(f"Git remote URL scheme is not supported: {scheme}")
    return value
