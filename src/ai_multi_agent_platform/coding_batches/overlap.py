"""Conservative dependency and overlap classification for coding work items."""

from __future__ import annotations

from .models import CodingWorkItem, OverlapDecision, OverlapKind

_GLOBAL_PATHS = {
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "docker-compose.yml",
    "compose.yml",
}
_GLOBAL_PREFIXES = (
    ".github/",
    "migrations/",
    "schema/",
    "schemas/",
)


def _ownership_roots(paths: tuple[str, ...]) -> set[str]:
    roots: set[str] = set()
    for path in paths:
        normalized = path.strip("/")
        if not normalized:
            continue
        roots.add(normalized.split("/", 1)[0])
    return roots


def _touches_global_contract(paths: tuple[str, ...]) -> bool:
    return any(path in _GLOBAL_PATHS or path.startswith(_GLOBAL_PREFIXES) for path in paths)


def _serialization_group(item: CodingWorkItem) -> str | None:
    value = item.metadata.get("serialization_group")
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class ConservativeOverlapClassifier:
    """Prove parallel safety where possible and fail closed when evidence is incomplete."""

    def classify(self, left: CodingWorkItem, right: CodingWorkItem) -> OverlapDecision:
        pair = (left.work_item_id, right.work_item_id)
        if left.work_item_id in right.dependencies or right.work_item_id in left.dependencies:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.DEPENDENCY,
                rationale="canonical work-item dependency requires predecessor completion",
            )

        left_serial = _serialization_group(left)
        right_serial = _serialization_group(right)
        if left_serial is not None and left_serial == right_serial:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.EXPLICIT_CONFLICT,
                rationale=(
                    "work items share explicit serialization group "
                    f"{left_serial!r} and must not run concurrently"
                ),
            )

        shared_paths = set(left.affected_paths) & set(right.affected_paths)
        if shared_paths:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.LIKELY_TEXTUAL_OVERLAP,
                rationale="declared affected paths overlap: " + ", ".join(sorted(shared_paths)),
            )

        shared_scopes = set(left.semantic_scopes) & set(right.semantic_scopes)
        if shared_scopes:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.SEMANTIC_OVERLAP,
                rationale="semantic ownership overlaps: " + ", ".join(sorted(shared_scopes)),
            )

        if _touches_global_contract(left.affected_paths) and _touches_global_contract(
            right.affected_paths
        ):
            return OverlapDecision(
                *pair,
                kind=OverlapKind.EXPLICIT_CONFLICT,
                rationale="both work items modify global configuration/schema surfaces",
            )

        if not left.affected_paths or not right.affected_paths:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.UNKNOWN,
                rationale="affected-path evidence is incomplete; parallel safety is unproven",
            )

        left_roots = _ownership_roots(left.affected_paths)
        right_roots = _ownership_roots(right.affected_paths)
        disjoint_roots = bool(left_roots and right_roots and left_roots.isdisjoint(right_roots))
        scopes_prove_separation = bool(
            left.semantic_scopes
            and right.semantic_scopes
            and set(left.semantic_scopes).isdisjoint(right.semantic_scopes)
        )
        if disjoint_roots:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.INDEPENDENT,
                rationale="work items have disjoint repository ownership roots",
            )
        if scopes_prove_separation:
            return OverlapDecision(
                *pair,
                kind=OverlapKind.SHARED_SOURCE_SAFE,
                rationale=(
                    "work items share a repository ownership root but have disjoint paths and "
                    "explicitly disjoint semantic scopes"
                ),
            )
        return OverlapDecision(
            *pair,
            kind=OverlapKind.UNKNOWN,
            rationale="repository ownership is potentially shared and safety is not proven",
        )

    def classify_all(self, items: tuple[CodingWorkItem, ...]) -> tuple[OverlapDecision, ...]:
        decisions: list[OverlapDecision] = []
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                decisions.append(self.classify(left, right))
        return tuple(decisions)
