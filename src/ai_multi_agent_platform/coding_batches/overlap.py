"""Deterministic, conservative overlap classification for coding workstreams."""

from __future__ import annotations

from itertools import combinations
from pathlib import PurePosixPath

from .models import CodingWorkItem, OverlapDecision, OverlapKind

_GLOBAL_FILES = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        "docker-compose.yml",
        "compose.yaml",
    }
)
_GLOBAL_PREFIXES = (".github/", "migrations/", "alembic/", "schemas/")


def _path(value: str) -> str:
    normalized = str(PurePosixPath(value.replace("\\", "/")))
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise ValueError("affected paths must be repository-relative")
    return normalized


def _ownership_root(path: str) -> tuple[str, ...]:
    parts = PurePosixPath(path).parts
    if not parts:
        return ()
    if parts[0] in {"src", "tests", "frontend"} and len(parts) > 1:
        return parts[:2]
    return parts[:1]


def _touches_global_contract(paths: set[str]) -> bool:
    return any(path in _GLOBAL_FILES or path.startswith(_GLOBAL_PREFIXES) for path in paths)


class ConservativeOverlapClassifier:
    """Classify only evidence-backed independence as parallel-safe.

    Optional #502 code intelligence can enrich ``semantic_scopes`` before this classifier is
    invoked.  Without it, exact changed-path hints plus coarse ownership roots provide the local
    deterministic baseline; ambiguous same-domain changes remain ``UNKNOWN``.
    """

    def classify(self, left: CodingWorkItem, right: CodingWorkItem) -> OverlapDecision:
        if right.work_item_id in left.dependencies or left.work_item_id in right.dependencies:
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.DEPENDENCY,
                "canonical work-item dependency requires ordered execution",
            )

        left_paths = {_path(path) for path in left.affected_paths}
        right_paths = {_path(path) for path in right.affected_paths}
        shared_paths = left_paths & right_paths
        if shared_paths:
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.TEXTUAL_CONFLICT,
                "declared affected paths overlap: " + ", ".join(sorted(shared_paths)),
            )

        left_scopes = set(left.semantic_scopes)
        right_scopes = set(right.semantic_scopes)
        shared_scopes = left_scopes & right_scopes
        if shared_scopes:
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.SEMANTIC_OVERLAP,
                "semantic ownership/impact scopes overlap: " + ", ".join(sorted(shared_scopes)),
            )

        if _touches_global_contract(left_paths) and _touches_global_contract(right_paths):
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.SEMANTIC_OVERLAP,
                "both work items modify global configuration, schema, migration or CI surfaces",
            )

        if not left_paths or not right_paths:
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.UNKNOWN,
                "insufficient affected-path evidence; independence is not proven",
            )

        if left_scopes and right_scopes:
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.INDEPENDENT,
                "affected paths and semantic scopes are disjoint",
            )

        left_roots = {_ownership_root(path) for path in left_paths}
        right_roots = {_ownership_root(path) for path in right_paths}
        if left_roots.isdisjoint(right_roots):
            return OverlapDecision(
                left.work_item_id,
                right.work_item_id,
                OverlapKind.INDEPENDENT,
                "deterministic repository ownership roots are disjoint",
            )

        return OverlapDecision(
            left.work_item_id,
            right.work_item_id,
            OverlapKind.UNKNOWN,
            "paths are distinct but remain inside a shared ownership root",
        )

    def classify_all(self, items: tuple[CodingWorkItem, ...]) -> tuple[OverlapDecision, ...]:
        return tuple(self.classify(left, right) for left, right in combinations(items, 2))
