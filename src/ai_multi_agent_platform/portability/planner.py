"""Dependency/conflict validation and mutation-free import preview for issue #79."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .dependencies import ResourceDependencyRef, parse_resource_dependency
from .models import (
    DependencyKind,
    DependencyRequirement,
    IdPolicy,
    PortablePackage,
    PortableResource,
)
from .package import verify_package

ResourceExists = Callable[[str, str], bool]
DependencyAvailable = Callable[[DependencyRequirement], bool]
NameConflict = Callable[[PortableResource], str | None]
IdAllocator = Callable[[PortablePackage, PortableResource, int], str]


class ImportConflictKind(StrEnum):
    ID_EXISTS = "id_exists"
    NAME_EXISTS = "name_exists"
    GENERATED_ID_EXISTS = "generated_id_exists"
    DEPENDENCY_CYCLE = "dependency_cycle"


@dataclass(frozen=True, slots=True)
class ImportConflict:
    kind: ImportConflictKind
    resource_type: str
    resource_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class MissingDependency:
    requirement: DependencyRequirement
    requested_by: str


@dataclass(frozen=True, slots=True)
class PlannedResource:
    resource_type: str
    source_id: str
    target_id: str
    resource_version: str
    id_policy: IdPolicy


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """Complete dry-run result. Producing this object never mutates destination state."""

    package_checksum: str
    ready: bool
    resources: tuple[PlannedResource, ...]
    import_order: tuple[tuple[str, str], ...]
    id_mapping: tuple[tuple[tuple[str, str], str], ...]
    missing_dependencies: tuple[MissingDependency, ...] = ()
    optional_missing_dependencies: tuple[MissingDependency, ...] = ()
    conflicts: tuple[ImportConflict, ...] = ()

    def mapping_dict(self) -> dict[tuple[str, str], str]:
        return dict(self.id_mapping)


class ImportPreviewService:
    """Validate a portable package against target state before any import mutation."""

    def __init__(
        self,
        *,
        resource_exists: ResourceExists,
        dependency_available: DependencyAvailable,
        name_conflict: NameConflict | None = None,
        id_allocator: IdAllocator | None = None,
        max_id_allocation_attempts: int = 32,
    ) -> None:
        if max_id_allocation_attempts < 1:
            raise ValueError("max_id_allocation_attempts must be >= 1")
        self._resource_exists = resource_exists
        self._dependency_available = dependency_available
        self._name_conflict = name_conflict
        self._id_allocator = id_allocator or _default_id_allocator
        self._max_id_allocation_attempts = max_id_allocation_attempts

    def preview(self, package: PortablePackage) -> ImportPreview:
        """Return conflicts, missing dependencies, mapping and dependency-safe order."""

        verify_package(package)
        mapping: dict[tuple[str, str], str] = {}
        planned: list[PlannedResource] = []
        conflicts: list[ImportConflict] = []
        reserved_targets: set[tuple[str, str]] = set()

        for resource in package.resources:
            source_key = (resource.resource_type, resource.resource_id)
            target_id = resource.resource_id
            if resource.id_policy is IdPolicy.REGENERATE:
                target_id = self._allocate_target_id(package, resource, reserved_targets)
                if self._resource_exists(resource.resource_type, target_id):
                    conflicts.append(
                        ImportConflict(
                            kind=ImportConflictKind.GENERATED_ID_EXISTS,
                            resource_type=resource.resource_type,
                            resource_id=resource.resource_id,
                            detail=f"generated target ID already exists: {target_id}",
                        )
                    )
            elif self._resource_exists(resource.resource_type, resource.resource_id):
                conflicts.append(
                    ImportConflict(
                        kind=ImportConflictKind.ID_EXISTS,
                        resource_type=resource.resource_type,
                        resource_id=resource.resource_id,
                        detail="target already contains the canonical resource ID",
                    )
                )

            target_key = (resource.resource_type, target_id)
            if target_key in reserved_targets:
                conflicts.append(
                    ImportConflict(
                        kind=ImportConflictKind.GENERATED_ID_EXISTS,
                        resource_type=resource.resource_type,
                        resource_id=resource.resource_id,
                        detail=f"multiple imported resources map to target ID {target_id}",
                    )
                )
            reserved_targets.add(target_key)
            mapping[source_key] = target_id
            planned.append(
                PlannedResource(
                    resource_type=resource.resource_type,
                    source_id=resource.resource_id,
                    target_id=target_id,
                    resource_version=resource.resource_version,
                    id_policy=resource.id_policy,
                )
            )

            if self._name_conflict is not None:
                conflicting_name = self._name_conflict(resource)
                if conflicting_name is not None:
                    conflicts.append(
                        ImportConflict(
                            kind=ImportConflictKind.NAME_EXISTS,
                            resource_type=resource.resource_type,
                            resource_id=resource.resource_id,
                            detail=conflicting_name,
                        )
                    )

        package_keys = {(item.resource_type, item.resource_id) for item in package.resources}
        required_missing: list[MissingDependency] = []
        optional_missing: list[MissingDependency] = []

        for requirement in package.manifest.requirements:
            self._classify_dependency(
                requirement,
                requested_by="package",
                package_keys=package_keys,
                required_missing=required_missing,
                optional_missing=optional_missing,
            )
        for resource in package.resources:
            requester = f"{resource.resource_type}:{resource.resource_id}"
            for requirement in resource.dependencies:
                self._classify_dependency(
                    requirement,
                    requested_by=requester,
                    package_keys=package_keys,
                    required_missing=required_missing,
                    optional_missing=optional_missing,
                )

        try:
            order = _dependency_order(package)
        except ContractError as exc:
            conflicts.append(
                ImportConflict(
                    kind=ImportConflictKind.DEPENDENCY_CYCLE,
                    resource_type="package",
                    resource_id=package.checksum,
                    detail=exc.message,
                )
            )
            order = tuple((item.resource_type, item.resource_id) for item in package.resources)

        ordered_mapping = tuple(sorted(mapping.items(), key=lambda item: item[0]))
        return ImportPreview(
            package_checksum=package.checksum,
            ready=not conflicts and not required_missing,
            resources=tuple(planned),
            import_order=order,
            id_mapping=ordered_mapping,
            missing_dependencies=tuple(required_missing),
            optional_missing_dependencies=tuple(optional_missing),
            conflicts=tuple(conflicts),
        )

    def _allocate_target_id(
        self,
        package: PortablePackage,
        resource: PortableResource,
        reserved_targets: set[tuple[str, str]],
    ) -> str:
        for attempt in range(self._max_id_allocation_attempts):
            candidate = self._id_allocator(package, resource, attempt)
            if not candidate.strip():
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "portable import ID allocator returned a blank ID",
                )
            key = (resource.resource_type, candidate)
            if key in reserved_targets:
                continue
            if not self._resource_exists(resource.resource_type, candidate):
                return candidate
        raise ContractError(
            ErrorCode.CONFLICT,
            "unable to allocate a free deterministic import ID",
            details={
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
                "attempts": self._max_id_allocation_attempts,
            },
        )

    def _classify_dependency(
        self,
        requirement: DependencyRequirement,
        *,
        requested_by: str,
        package_keys: set[tuple[str, str]],
        required_missing: list[MissingDependency],
        optional_missing: list[MissingDependency],
    ) -> None:
        available = False
        if requirement.kind is DependencyKind.RESOURCE:
            reference = parse_resource_dependency(requirement)
            available = (reference.resource_type, reference.resource_id) in package_keys
        if not available:
            available = self._dependency_available(requirement)
        if available:
            return
        missing = MissingDependency(requirement=requirement, requested_by=requested_by)
        if requirement.required:
            required_missing.append(missing)
        else:
            optional_missing.append(missing)


def _default_id_allocator(
    package: PortablePackage,
    resource: PortableResource,
    attempt: int,
) -> str:
    """Stable package-local clone ID while preserving canonical prefix shape when present."""

    seed = f"{package.checksum}:{resource.resource_type}:{resource.resource_id}:{attempt}"
    generated = uuid5(NAMESPACE_URL, seed)
    prefix, separator, _ = resource.resource_id.partition("_")
    if separator and prefix:
        return f"{prefix}_{generated}"
    normalized_type = resource.resource_type.replace(".", "-").replace(":", "-")
    return f"{normalized_type}-{generated}"


def _dependency_order(package: PortablePackage) -> tuple[tuple[str, str], ...]:
    """Return a stable topological order for dependencies carried in this package."""

    keys = [(item.resource_type, item.resource_id) for item in package.resources]
    key_set = set(keys)
    outgoing: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    indegree = {key: 0 for key in keys}

    for resource in package.resources:
        dependent = (resource.resource_type, resource.resource_id)
        for requirement in resource.dependencies:
            if requirement.kind is not DependencyKind.RESOURCE:
                continue
            reference: ResourceDependencyRef = parse_resource_dependency(requirement)
            dependency = (reference.resource_type, reference.resource_id)
            if dependency not in key_set or dependency == dependent:
                if dependency == dependent:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "portable resource dependency cycle contains a self-reference",
                        details={"resource_type": dependent[0], "resource_id": dependent[1]},
                    )
                continue
            if dependent not in outgoing[dependency]:
                outgoing[dependency].add(dependent)
                indegree[dependent] += 1

    queue = deque(sorted(key for key, count in indegree.items() if count == 0))
    ordered: list[tuple[str, str]] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for dependent in sorted(outgoing[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(ordered) != len(keys):
        remaining = sorted(key for key, count in indegree.items() if count > 0)
        raise ContractError(
            ErrorCode.CONFLICT,
            "portable package contains a resource dependency cycle",
            details={"resources": [f"{kind}:{resource_id}" for kind, resource_id in remaining]},
        )
    return tuple(ordered)
