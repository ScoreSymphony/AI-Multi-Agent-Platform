"""Dependency-free local Git reference adapter for canonical repository contracts."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ai_multi_agent_platform.connectors import ExternalNativeReference, ExternalResourceReference
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id

from .capabilities import LOCAL_GIT_CAPABILITIES
from .contracts import RepositoryProvider
from .models import (
    RepositoryCommit,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryReference,
    RepositoryRevision,
    RepositoryStatus,
    RepositoryTree,
    RepositoryTreeEntry,
    RepositoryVisibility,
)


class LocalGitRepositoryProvider(RepositoryProvider):
    """One local repository binding; filesystem paths remain adapter-private implementation data."""

    def __init__(
        self,
        root: str | Path,
        connection: RepositoryConnection,
        *,
        git_binary: str = "git",
        repository: RepositoryReference | None = None,
        provider_id: str = "local-git",
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._connection = connection
        self._git_binary = git_binary
        self._repository = repository
        self._provider_id = provider_id
        if not provider_id.strip():
            raise ValueError("local Git provider_id must not be blank")
        if not git_binary.strip():
            raise ValueError("git_binary must not be blank")
        if not connection.local:
            raise ValueError("LocalGitRepositoryProvider requires a local RepositoryConnection")
        if repository is not None and repository.connection_id != connection.id:
            raise ValueError("repository and connection IDs must match")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    async def initialize(
        self,
        context: OperationContext,
        *,
        default_branch: str = "main",
        repository_id: str | None = None,
    ) -> RepositoryReference:
        del context
        if not default_branch.strip():
            raise ValueError("default_branch must not be blank")
        self._root.mkdir(parents=True, exist_ok=True)
        if (self._root / ".git").exists():
            return await self.open(OperationContext(correlation_id="local-git-open"))
        self._run("init", "-b", default_branch)
        reference = self._new_reference(default_branch=default_branch, repository_id=repository_id)
        self._repository = reference
        return reference

    async def open(self, context: OperationContext) -> RepositoryReference:
        del context
        self._run("rev-parse", "--git-dir")
        branch = self._text("symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True)
        branch = branch.strip() or None
        head = self._head_revision()
        if self._repository is None:
            self._repository = self._new_reference(default_branch=branch)
        reference = self._repository
        if branch is not None and reference.default_branch is None:
            reference = replace(reference, default_branch=branch)
        reference = self._with_revision(reference, head)
        self._repository = reference
        return reference

    async def discover(
        self,
        connection: RepositoryConnection,
        context: OperationContext,
    ) -> tuple[RepositoryReference, ...]:
        if connection.id != self._connection.id:
            return ()
        try:
            return (await self.open(context),)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return ()
            raise

    async def read(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryReference:
        self._require_repository(repository)
        del context
        head = self._head_revision()
        refreshed = self._with_revision(repository, head)
        self._repository = refreshed
        return refreshed

    async def resolve_revision(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        self._require_repository(repository)
        del context
        if not revision.strip():
            raise ValueError("revision must not be blank")
        commit_sha = self._text("rev-parse", "--verify", f"{revision}^{{commit}}").strip()
        return RepositoryRevision(repository.id, revision, commit_sha)

    async def read_tree(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        resolved = await self.resolve_revision(repository, revision, context)
        raw = self._run("ls-tree", "-r", "-z", resolved.commit_sha).stdout
        entries: list[RepositoryTreeEntry] = []
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                header, raw_path = record.split(b"\t", 1)
                mode, object_type, _object_sha = header.decode("ascii").split(" ", 2)
                path = raw_path.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "Git tree contains a path or entry the canonical workspace cannot represent",
                    provider_id=self.provider_id,
                ) from exc
            if mode == "120000":
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    f"symbolic links are not materialized into canonical workspaces: {path}",
                    provider_id=self.provider_id,
                )
            if object_type != "blob":
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    f"non-file Git tree entry is not supported: {path}",
                    provider_id=self.provider_id,
                )
            data = self._run("show", f"{resolved.commit_sha}:{path}").stdout
            entries.append(RepositoryTreeEntry(path, data))
        return RepositoryTree(
            repository_id=repository.id,
            requested_ref=revision,
            resolved_revision=resolved.commit_sha,
            entries=tuple(entries),
        )

    async def branches(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        self._require_repository(repository)
        del context
        return self._lines("for-each-ref", "--format=%(refname:short)", "refs/heads")

    async def tags(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        self._require_repository(repository)
        del context
        return self._lines("tag", "--list")

    async def status(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryStatus:
        self._require_repository(repository)
        del context
        lines = self._text("status", "--porcelain=v1", "--branch").splitlines()
        branch: str | None = None
        staged: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        untracked: list[str] = []
        for line in lines:
            if line.startswith("## "):
                branch_text = line[3:].split("...", 1)[0].strip()
                if branch_text and branch_text != "HEAD (no branch)":
                    branch = branch_text
                continue
            if len(line) < 4:
                continue
            code = line[:2]
            path = line[3:]
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            if code == "??":
                untracked.append(path)
                continue
            if code[0] != " ":
                staged.append(path)
            if code[1] in {"M", "T", "U"}:
                modified.append(path)
            if "D" in code:
                deleted.append(path)
        return RepositoryStatus(
            repository_id=repository.id,
            head_revision=self._head_revision(),
            branch=branch,
            staged_paths=tuple(sorted(set(staged))),
            modified_paths=tuple(sorted(set(modified))),
            deleted_paths=tuple(sorted(set(deleted))),
            untracked_paths=tuple(sorted(set(untracked))),
        )

    async def diff(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        base_revision: str | None = None,
    ) -> RepositoryDiff:
        self._require_repository(repository)
        del context
        selected_base = base_revision
        if selected_base is None:
            selected_base = self._head_revision()
        if selected_base is None:
            return RepositoryDiff(repository.id, None, "", ())
        resolved = self._text("rev-parse", "--verify", f"{selected_base}^{{commit}}").strip()
        patch = self._run("diff", "--binary", resolved, "--").stdout.decode(
            "utf-8", errors="replace"
        )
        paths = self._lines("diff", "--name-only", resolved, "--")
        return RepositoryDiff(repository.id, resolved, patch, paths)

    async def create_branch(
        self,
        repository: RepositoryReference,
        name: str,
        context: OperationContext,
        *,
        start_revision: str = "HEAD",
        checkout: bool = False,
    ) -> RepositoryRevision:
        self._require_repository(repository)
        del context
        if not name.strip():
            raise ValueError("branch name must not be blank")
        self._run("check-ref-format", "--branch", name)
        start = self._text("rev-parse", "--verify", f"{start_revision}^{{commit}}").strip()
        if checkout:
            self._run("checkout", "-b", name, start)
        else:
            self._run("branch", name, start)
        return RepositoryRevision(repository.id, name, start)

    async def checkout(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        self._require_repository(repository)
        del context
        resolved = self._text("rev-parse", "--verify", f"{revision}^{{commit}}").strip()
        self._run("checkout", revision)
        return RepositoryRevision(repository.id, revision, resolved)

    async def commit(
        self,
        repository: RepositoryReference,
        message: str,
        context: OperationContext,
        *,
        author_name: str,
        author_email: str,
    ) -> RepositoryCommit:
        self._require_repository(repository)
        del context
        if not message.strip() or not author_name.strip() or not author_email.strip():
            raise ValueError("commit message, author name and author email must not be blank")
        self._run("add", "--all")
        self._run(
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        )
        revision = self._text("rev-parse", "HEAD").strip()
        parent_line = self._text("rev-list", "--parents", "-n", "1", "HEAD").strip().split()
        parents = tuple(parent_line[1:])
        return RepositoryCommit(repository.id, revision, message, parents)

    async def fetch(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryRevision | None:
        self._require_repository(repository)
        del context
        if not self._lines("remote"):
            head = self._head_revision()
            return RepositoryRevision(repository.id, "HEAD", head) if head is not None else None
        self._run("fetch", "--all", "--prune")
        head = self._head_revision()
        return RepositoryRevision(repository.id, "HEAD", head) if head is not None else None

    async def push(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        remote: str = "origin",
        refspec: str | None = None,
    ) -> RepositoryRevision:
        self._require_repository(repository)
        del context
        if not remote.strip():
            raise ValueError("remote must not be blank")
        args = ["push", remote]
        if refspec is not None:
            if not refspec.strip():
                raise ValueError("refspec must not be blank when provided")
            args.append(refspec)
        self._run(*args)
        head = self._head_revision()
        if head is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "push completed but repository has no HEAD commit",
                provider_id=self.provider_id,
            )
        return RepositoryRevision(repository.id, refspec or "HEAD", head)

    def _new_reference(
        self,
        *,
        default_branch: str | None,
        repository_id: str | None = None,
    ) -> RepositoryReference:
        external = ExternalResourceReference(
            id=repository_id or new_id("external_resource"),
            connection_id=self._connection.id,
            resource_type="repository",
            native_reference=ExternalNativeReference(
                namespace="local-git",
                native_id=f"repository-{uuid4()}",
            ),
            provenance={"provider": "local_git"},
            metadata={"transport": "local_git"},
        )
        return RepositoryReference(
            external_resource=external,
            default_branch=default_branch,
            visibility=RepositoryVisibility.LOCAL,
            capabilities=LOCAL_GIT_CAPABILITIES,
            metadata={"provider": self.provider_id},
        )

    def _with_revision(
        self,
        repository: RepositoryReference,
        revision: str | None,
    ) -> RepositoryReference:
        external = replace(repository.external_resource, revision=revision)
        return replace(repository, external_resource=external, resolved_revision=revision)

    def _require_repository(self, repository: RepositoryReference) -> None:
        if self._repository is None or repository.id != self._repository.id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"repository is not bound to provider {self.provider_id}: {repository.id}",
                provider_id=self.provider_id,
            )
        if repository.connection_id != self._connection.id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository connection does not match local provider binding",
                provider_id=self.provider_id,
            )

    def _head_revision(self) -> str | None:
        value = self._text("rev-parse", "--verify", "HEAD^{commit}", allow_failure=True).strip()
        return value or None

    def _lines(self, *args: str) -> tuple[str, ...]:
        value = self._text(*args)
        return tuple(line.strip() for line in value.splitlines() if line.strip())

    def _text(self, *args: str, allow_failure: bool = False) -> str:
        completed = self._run(*args, allow_failure=allow_failure)
        if allow_failure and completed.returncode != 0:
            return ""
        return completed.stdout.decode("utf-8", errors="replace")

    def _run(self, *args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                [self._git_binary, *args],
                cwd=self._root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Git executable is unavailable",
                retryable=False,
                provider_id=self.provider_id,
                details={"binary": self._git_binary},
            ) from exc
        if completed.returncode == 0 or allow_failure:
            return completed
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        lowered = stderr.lower()
        if "not a git repository" in lowered:
            code = ErrorCode.NOT_FOUND
        elif "already exists" in lowered or "nothing to commit" in lowered:
            code = ErrorCode.CONFLICT
        elif "authentication failed" in lowered or "permission denied" in lowered:
            code = ErrorCode.UNAUTHORIZED
        else:
            code = ErrorCode.BACKEND_ERROR
        raise ContractError(
            code,
            stderr or f"Git command failed: {' '.join(args)}",
            retryable=False,
            provider_id=self.provider_id,
            details={
                "git_exit_code": completed.returncode,
                "operation": args[0] if args else "git",
            },
        )
