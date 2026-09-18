from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.release.discovery import UpdateDiscoveryError
from ai_multi_agent_platform.release.providers import git_head_revision
from ai_multi_agent_platform.repositories import LocalGitRepositoryProvider, RepositoryConnection
from ai_multi_agent_platform.security.git_execution import (
    controlled_git_environment,
    unsafe_local_git_config_keys,
    validate_git_remote_url,
)


def _git_binary() -> str:
    binary = shutil.which("git")
    if binary is None:
        pytest.skip("Git is required for GitSpawn regression coverage")
    return binary


def _operation(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-1220-gitspawn",
        owner_type="user",
        owner_id="gitspawn-user",
        project_id=project_id,
    )


def _connection(project_id: str) -> RepositoryConnection:
    return RepositoryConnection(
        connection=Connection(
            id=new_id("connection"),
            connector_type_id="local-git",
            connector_version="1.0",
            owner_type="user",
            owner_id="gitspawn-user",
            display_name="GitSpawn regression fixture",
            project_id=project_id,
        ),
        provider_id="local-git",
        local=True,
    )


async def _initialized_provider(
    tmp_path: Path,
) -> tuple[LocalGitRepositoryProvider, object, OperationContext, Path]:
    project_id = new_id("project")
    operation = _operation(project_id)
    root = tmp_path / "repo"
    provider = LocalGitRepositoryProvider(root, _connection(project_id), git_binary=_git_binary())
    repository = await provider.initialize(operation)
    return provider, repository, operation, root


def _raw_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    home = root.parent / "fixture-home"
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [_git_binary(), *args],
        cwd=root,
        env=controlled_git_environment(home=home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )


def _python_marker_command(marker: Path) -> str:
    program = (
        "from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"


def _write_python_hook(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_gitspawn_environment_scrubs_execution_indirection() -> None:
    inherited = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_DIR": "/attacker/repository",
        "GIT_WORK_TREE": "/attacker/worktree",
        "GIT_EXEC_PATH": "/attacker/bin",
        "GIT_EXTERNAL_DIFF": "/attacker/diff",
        "GIT_SSH_COMMAND": "/attacker/ssh",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/attacker/hooks",
        "GIT_CONFIG_PARAMETERS": "'core.hooksPath'='/attacker/hooks'",
    }

    environment = controlled_git_environment(inherited)

    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_SSH_COMMAND",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS",
    ):
        assert key not in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize(
    "key",
    (
        "core.hooksPath",
        "filter.attack.clean",
        "filter.attack.smudge",
        "filter.attack.process",
        "diff.attack.command",
        "diff.attack.textconv",
        "credential.helper",
        "credential.example.helper",
        "include.path",
        "includeIf.gitdir:repo.path",
        "url.attack.insteadOf",
        "url.attack.pushInsteadOf",
        "remote.origin.proxy",
        "remote.origin.receivepack",
        "remote.origin.uploadpack",
        "submodule.attack.update",
        "core.sshCommand",
        "core.gitProxy",
        "gpg.program",
    ),
)
def test_gitspawn_execution_capable_local_config_inventory(key: str) -> None:
    assert unsafe_local_git_config_keys((key,)) == (key.lower(),)


@pytest.mark.parametrize(
    "url",
    (
        "ext::touch /tmp/issue-1220-marker",
        "evil::touch /tmp/issue-1220-marker",
        "evil://repository.example.invalid/project",
    ),
)
def test_gitspawn_external_remote_helpers_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        validate_git_remote_url(url)


@pytest.mark.skipif(os.name == "nt", reason="Git hook executability semantics differ on Windows")
def test_gitspawn_default_repository_hook_is_isolated(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        marker = tmp_path / "hook-executed"
        _write_python_hook(root / ".git" / "hooks" / "pre-commit", marker)
        (root / "payload.txt").write_text("safe\n", encoding="utf-8")

        await provider.commit(
            repository,
            "safe commit",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )

        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="Git hook executability semantics differ on Windows")
def test_gitspawn_inherited_global_hooks_path_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        marker = tmp_path / "global-hook-executed"
        hooks = tmp_path / "global-hooks"
        _write_python_hook(hooks / "pre-commit", marker)
        global_config = tmp_path / "global.gitconfig"
        global_config.write_text(
            f"[core]\n\thooksPath = {hooks.as_posix()}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

        provider, repository, operation, root = await _initialized_provider(tmp_path)
        (root / "payload.txt").write_text("safe\n", encoding="utf-8")
        await provider.commit(
            repository,
            "global config must not execute",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )

        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="Git hook executability semantics differ on Windows")
def test_gitspawn_environment_config_injection_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        marker = tmp_path / "env-hook-executed"
        hooks = tmp_path / "env-hooks"
        _write_python_hook(hooks / "pre-commit", marker)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))

        provider, repository, operation, root = await _initialized_provider(tmp_path)
        (root / "payload.txt").write_text("safe\n", encoding="utf-8")
        await provider.commit(
            repository,
            "environment config must not execute",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )

        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="fixture commands use POSIX shell command parsing")
def test_gitspawn_local_clean_filter_is_rejected_before_add(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        marker = tmp_path / "filter-executed"
        _raw_git(root, "config", "--local", "filter.attack.clean", _python_marker_command(marker))
        _raw_git(root, "config", "--local", "filter.attack.smudge", _python_marker_command(marker))
        (root / ".gitattributes").write_text("payload.txt filter=attack\n", encoding="utf-8")
        (root / "payload.txt").write_text("unsafe filter fixture\n", encoding="utf-8")

        with pytest.raises(ContractError) as error:
            await provider.commit(
                repository,
                "must reject filter",
                operation,
                author_name="GitSpawn Test",
                author_email="gitspawn@example.invalid",
            )

        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="fixture commands use POSIX shell command parsing")
def test_gitspawn_local_textconv_is_rejected_before_diff(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        (root / "payload.txt").write_text("one\n", encoding="utf-8")
        first = await provider.commit(
            repository,
            "baseline",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )

        marker = tmp_path / "textconv-executed"
        _raw_git(root, "config", "--local", "diff.attack.textconv", _python_marker_command(marker))
        (root / ".gitattributes").write_text("payload.txt diff=attack\n", encoding="utf-8")
        (root / "payload.txt").write_text("two\n", encoding="utf-8")

        with pytest.raises(ContractError) as error:
            await provider.diff(repository, operation, base_revision=first.revision)

        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
        assert not marker.exists()

    asyncio.run(scenario())


def test_gitspawn_config_include_is_rejected_without_loading_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        marker = tmp_path / "included-hook-executed"
        hooks = tmp_path / "included-hooks"
        extra = tmp_path / "attacker.gitconfig"
        extra.write_text(
            f"[core]\n\thooksPath = {hooks.as_posix()}\n",
            encoding="utf-8",
        )
        _raw_git(root, "config", "--local", "include.path", str(extra))

        with pytest.raises(ContractError) as error:
            await provider.status(repository, operation)

        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="external-diff fixture uses executable POSIX semantics")
def test_gitspawn_external_diff_environment_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        (root / "payload.txt").write_text("one\n", encoding="utf-8")
        first = await provider.commit(
            repository,
            "baseline",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )
        marker = tmp_path / "external-diff-executed"
        external_diff = tmp_path / "external-diff"
        _write_python_hook(external_diff, marker)
        monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(external_diff))
        (root / "payload.txt").write_text("two\n", encoding="utf-8")

        diff = await provider.diff(repository, operation, base_revision=first.revision)

        assert "payload.txt" in diff.changed_paths
        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="remote-helper fixture uses POSIX command syntax")
def test_gitspawn_external_remote_helper_is_rejected_before_fetch(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        marker = tmp_path / "remote-helper-executed"
        _raw_git(root, "remote", "add", "origin", f"ext::touch {marker}")

        with pytest.raises(ContractError) as error:
            await provider.fetch(repository, operation)

        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
        assert not marker.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="remote-helper fixture uses POSIX command syntax")
def test_gitspawn_release_discovery_rejects_remote_helper_before_spawn(tmp_path: Path) -> None:
    marker = tmp_path / "release-remote-helper-executed"

    with pytest.raises(UpdateDiscoveryError):
        git_head_revision(f"ext::touch {marker}")

    assert not marker.exists()


def test_gitspawn_benign_repository_operations_remain_supported(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider, repository, operation, root = await _initialized_provider(tmp_path)
        (root / "payload.txt").write_text("one\n", encoding="utf-8")
        first = await provider.commit(
            repository,
            "baseline",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )
        branch = await provider.create_branch(
            repository,
            "issue-1220-control",
            operation,
            start_revision=first.revision,
            checkout=True,
        )
        assert branch.commit_sha == first.revision

        (root / "payload.txt").write_text("two\n", encoding="utf-8")
        status = await provider.status(repository, operation)
        assert status.modified_paths == ("payload.txt",)
        diff = await provider.diff(repository, operation, base_revision=first.revision)
        assert diff.changed_paths == ("payload.txt",)

        second = await provider.commit(
            repository,
            "second",
            operation,
            author_name="GitSpawn Test",
            author_email="gitspawn@example.invalid",
        )
        assert second.revision != first.revision
        assert (await provider.status(repository, operation)).clean

    asyncio.run(scenario())
