"""Cross-kind Marketplace dependency graph and environment compatibility."""

from __future__ import annotations

from dataclasses import dataclass

from .decision_types import (
    CompatibilityDecision,
    DependencyResolution,
    DependencyStatus,
)
from .items import InstalledRegistryItem, RegistryItem
from .models import RegistryDependency, VersionRange, version_key
from .validation import FindingCategory, FindingSeverity, ValidationContext, ValidationFinding


@dataclass(frozen=True, slots=True)
class _DependencyVisit:
    resolution: DependencyResolution
    next_item: RegistryItem | None = None


def evaluate_compatibility(
    item: RegistryItem,
    context: ValidationContext,
) -> CompatibilityDecision:
    compatibility = item.compatibility
    return CompatibilityDecision(
        platform_compatible=item.supported_platform.contains(context.platform_version),
        operating_system_compatible=_environment_value_supported(
            compatibility.operating_systems,
            context.operating_system,
        ),
        architecture_compatible=_environment_value_supported(
            compatibility.architectures,
            context.architecture,
        ),
        missing_runtimes=tuple(
            sorted(compatibility.required_runtimes - context.available_runtimes)
        ),
        missing_capabilities=tuple(
            sorted(item.required_capabilities - context.available_capabilities)
        ),
        missing_plugins=tuple(sorted(set(item.required_plugins) - context.installed_plugins)),
        missing_connectors=tuple(
            sorted(set(item.required_connectors) - context.installed_connectors)
        ),
        missing_models=tuple(sorted(set(item.required_models) - context.available_models)),
    )


def resolve_dependency_graph(
    item: RegistryItem,
    *,
    catalog: tuple[RegistryItem, ...],
    installed_items: tuple[InstalledRegistryItem, ...],
) -> tuple[DependencyResolution, ...]:
    installed = {record.item_id: record for record in installed_items}
    resolutions: list[DependencyResolution] = []
    _walk_item(
        item,
        path=(item.item_id,),
        catalog=catalog,
        installed=installed,
        resolutions=resolutions,
    )
    _append_constraint_conflicts(
        item,
        resolutions=resolutions,
        catalog=catalog,
        installed=installed,
    )
    return tuple(resolutions)


def _walk_item(
    parent: RegistryItem,
    *,
    path: tuple[str, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    resolutions: list[DependencyResolution],
) -> None:
    for dependency in parent.dependencies:
        visit = _resolve_dependency(
            parent,
            dependency,
            path=path,
            catalog=catalog,
            installed=installed,
        )
        resolutions.append(visit.resolution)
        if dependency.optional or visit.next_item is None:
            continue
        _walk_item(
            visit.next_item,
            path=(*path, dependency.item_id),
            catalog=catalog,
            installed=installed,
            resolutions=resolutions,
        )


def _resolve_dependency(
    parent: RegistryItem,
    dependency: RegistryDependency,
    *,
    path: tuple[str, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
) -> _DependencyVisit:
    dependency_path = (*path, dependency.item_id)
    if dependency.item_id == parent.item_id:
        return _DependencyVisit(
            _resolution(
                parent,
                dependency,
                DependencyStatus.SELF_DEPENDENCY,
                path=dependency_path,
            )
        )
    if dependency.item_id in path:
        return _DependencyVisit(
            _resolution(
                parent,
                dependency,
                DependencyStatus.CYCLE,
                path=dependency_path,
            )
        )

    record = installed.get(dependency.item_id)
    candidate, catalog_status = _select_dependency_candidate(
        dependency,
        catalog,
        preferred_source=record.source_registry if record is not None else None,
        preferred_version=record.version if record is not None else None,
    )
    if record is not None:
        return _installed_visit(
            parent,
            dependency,
            record=record,
            candidate=candidate,
            path=dependency_path,
        )

    status = catalog_status
    if dependency.optional and status is DependencyStatus.MISSING:
        status = DependencyStatus.OPTIONAL_MISSING
    return _DependencyVisit(
        _resolution(
            parent,
            dependency,
            status,
            candidate=candidate,
            path=dependency_path,
        ),
        candidate if status is DependencyStatus.AVAILABLE else None,
    )


def _installed_visit(
    parent: RegistryItem,
    dependency: RegistryDependency,
    *,
    record: InstalledRegistryItem,
    candidate: RegistryItem | None,
    path: tuple[str, ...],
) -> _DependencyVisit:
    status = _installed_dependency_status(dependency, record)
    next_item = candidate if status is DependencyStatus.SATISFIED else None
    return _DependencyVisit(
        _resolution(
            parent,
            dependency,
            status,
            record=record,
            candidate=candidate,
            path=path,
        ),
        next_item,
    )


def _installed_dependency_status(
    dependency: RegistryDependency,
    record: InstalledRegistryItem,
) -> DependencyStatus:
    if dependency.kind_value is not None:
        if record.kind is None:
            return DependencyStatus.KIND_UNKNOWN
        if record.kind != dependency.kind_value:
            return DependencyStatus.KIND_CONFLICT
    if not dependency.version_range.contains(record.version):
        return DependencyStatus.VERSION_CONFLICT
    return DependencyStatus.SATISFIED


def _select_dependency_candidate(
    dependency: RegistryDependency,
    catalog: tuple[RegistryItem, ...],
    *,
    preferred_source: str | None,
    preferred_version: str | None,
) -> tuple[RegistryItem | None, DependencyStatus]:
    by_id = tuple(candidate for candidate in catalog if candidate.item_id == dependency.item_id)
    if not by_id:
        return None, DependencyStatus.MISSING

    by_kind = _matching_kind_candidates(dependency, by_id)
    if not by_kind:
        return None, DependencyStatus.KIND_CONFLICT

    compatible = tuple(
        candidate
        for candidate in by_kind
        if dependency.version_range.contains(candidate.version) and not candidate.yanked
    )
    if not compatible:
        return None, DependencyStatus.VERSION_CONFLICT

    preferred = _preferred_candidate(
        compatible,
        source_registry=preferred_source,
        version=preferred_version,
    )
    if preferred is not None:
        return preferred, DependencyStatus.AVAILABLE
    return _latest_unambiguous_candidate(compatible)


def _matching_kind_candidates(
    dependency: RegistryDependency,
    candidates: tuple[RegistryItem, ...],
) -> tuple[RegistryItem, ...]:
    if dependency.kind_value is None:
        return candidates
    return tuple(candidate for candidate in candidates if candidate.kind == dependency.kind_value)


def _preferred_candidate(
    candidates: tuple[RegistryItem, ...],
    *,
    source_registry: str | None,
    version: str | None,
) -> RegistryItem | None:
    if source_registry is None:
        return None
    same_source = tuple(
        candidate for candidate in candidates if candidate.source_registry == source_registry
    )
    if version is not None:
        exact = next(
            (candidate for candidate in same_source if candidate.version == version),
            None,
        )
        if exact is not None:
            return exact
    if not same_source:
        return None
    return max(same_source, key=lambda candidate: version_key(candidate.version))


def _latest_unambiguous_candidate(
    candidates: tuple[RegistryItem, ...],
) -> tuple[RegistryItem | None, DependencyStatus]:
    latest_version = max(candidates, key=lambda item: version_key(item.version)).version
    latest = tuple(candidate for candidate in candidates if candidate.version == latest_version)
    if len({candidate.source_registry for candidate in latest}) > 1:
        return None, DependencyStatus.SOURCE_AMBIGUOUS
    return latest[0], DependencyStatus.AVAILABLE


def _append_constraint_conflicts(
    root: RegistryItem,
    *,
    resolutions: list[DependencyResolution],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
) -> None:
    grouped: dict[str, list[DependencyResolution]] = {}
    for resolution in resolutions:
        if resolution.optional:
            continue
        grouped.setdefault(resolution.item_id, []).append(resolution)

    for item_id, requirements in grouped.items():
        conflict = _constraint_conflict(
            root,
            item_id=item_id,
            requirements=tuple(requirements),
            catalog=catalog,
            installed=installed,
        )
        if conflict is not None:
            resolutions.append(conflict)


def _constraint_conflict(
    root: RegistryItem,
    *,
    item_id: str,
    requirements: tuple[DependencyResolution, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
) -> DependencyResolution | None:
    if len(requirements) < 2:
        return None
    kinds = {
        requirement.item_kind for requirement in requirements if requirement.item_kind is not None
    }
    if len(kinds) > 1:
        return _aggregate_conflict(root, item_id, DependencyStatus.KIND_CONFLICT)

    ranges = _constraint_ranges(requirements)
    required_kind = next(iter(kinds), None)
    record = installed.get(item_id)
    if _installed_record_satisfies(record, ranges, required_kind):
        return None
    if _catalog_satisfies_all(catalog, item_id, ranges, required_kind):
        return None
    if _already_has_terminal_graph_error(requirements):
        return None
    return _aggregate_conflict(
        root,
        item_id,
        DependencyStatus.VERSION_CONFLICT,
        item_kind=required_kind,
        minimum_version=_intersection_minimum(ranges),
        maximum_version=_intersection_maximum(ranges),
        installed_version=record.version if record is not None else None,
    )


def _constraint_ranges(
    requirements: tuple[DependencyResolution, ...],
) -> tuple[VersionRange, ...]:
    return tuple(
        VersionRange(requirement.minimum_version, requirement.maximum_version)
        for requirement in requirements
    )


def _installed_record_satisfies(
    record: InstalledRegistryItem | None,
    ranges: tuple[VersionRange, ...],
    required_kind: str | None,
) -> bool:
    if record is None:
        return False
    if required_kind is not None and record.kind != required_kind:
        return False
    return all(version_range.contains(record.version) for version_range in ranges)


def _catalog_satisfies_all(
    catalog: tuple[RegistryItem, ...],
    item_id: str,
    ranges: tuple[VersionRange, ...],
    required_kind: str | None,
) -> bool:
    candidates = (
        candidate
        for candidate in catalog
        if candidate.item_id == item_id
        and not candidate.yanked
        and (required_kind is None or candidate.kind == required_kind)
    )
    return any(
        all(version_range.contains(candidate.version) for version_range in ranges)
        for candidate in candidates
    )


def _already_has_terminal_graph_error(
    requirements: tuple[DependencyResolution, ...],
) -> bool:
    terminal = {
        DependencyStatus.MISSING,
        DependencyStatus.SOURCE_AMBIGUOUS,
        DependencyStatus.SELF_DEPENDENCY,
        DependencyStatus.CYCLE,
    }
    return any(requirement.status in terminal for requirement in requirements)


def _aggregate_conflict(
    root: RegistryItem,
    item_id: str,
    status: DependencyStatus,
    *,
    item_kind: str | None = None,
    minimum_version: str | None = None,
    maximum_version: str | None = None,
    installed_version: str | None = None,
) -> DependencyResolution:
    return DependencyResolution(
        required_by=root.item_id,
        item_id=item_id,
        item_kind=item_kind,
        optional=False,
        minimum_version=minimum_version,
        maximum_version=maximum_version,
        status=status,
        installed_version=installed_version,
        path=(root.item_id, item_id),
    )


def _intersection_minimum(ranges: tuple[VersionRange, ...]) -> str | None:
    values = tuple(version_range.minimum for version_range in ranges if version_range.minimum)
    return max(values, key=version_key) if values else None


def _intersection_maximum(ranges: tuple[VersionRange, ...]) -> str | None:
    values = tuple(version_range.maximum for version_range in ranges if version_range.maximum)
    return min(values, key=version_key) if values else None


def dependency_findings(
    resolutions: tuple[DependencyResolution, ...],
) -> tuple[ValidationFinding, ...]:
    return tuple(
        finding
        for resolution in resolutions
        if resolution.status is not DependencyStatus.SATISFIED
        for finding in (_dependency_finding(resolution),)
    )


def _dependency_finding(resolution: DependencyResolution) -> ValidationFinding:
    if resolution.optional:
        return _finding(
            f"optional_dependency_{_optional_status_name(resolution.status)}",
            FindingSeverity.WARNING,
            (
                f"optional dependency {resolution.item_id} is not satisfied "
                f"({resolution.status.value})"
            ),
            resolution,
        )

    code, message = _required_finding_spec(resolution)
    return _finding(code, FindingSeverity.ERROR, message, resolution)


def _optional_status_name(status: DependencyStatus) -> str:
    if status is DependencyStatus.OPTIONAL_MISSING:
        return "missing"
    if status is DependencyStatus.AVAILABLE:
        return "not_installed"
    return status.value


def _required_finding_spec(
    resolution: DependencyResolution,
) -> tuple[str, str]:
    item_id = resolution.item_id
    status = resolution.status
    if status is DependencyStatus.CYCLE:
        return "dependency_cycle", "dependency cycle detected: " + " -> ".join(resolution.path)
    if status is DependencyStatus.REQUIRED_BY_INSTALLED:
        return (
            "required_by_installed",
            f"installed component {resolution.required_by} requires {item_id}",
        )
    if status is DependencyStatus.UNKNOWN_INSTALLED_DEPENDENT:
        return (
            "installed_dependency_state_unknown",
            f"cannot verify dependencies for installed component {resolution.required_by}",
        )

    specs = {
        DependencyStatus.AVAILABLE: (
            "dependency_not_installed",
            f"dependency {item_id} is available but not installed",
        ),
        DependencyStatus.OPTIONAL_MISSING: (
            "missing_dependency",
            f"dependency {item_id} is missing",
        ),
        DependencyStatus.MISSING: (
            "missing_dependency",
            f"dependency {item_id} is missing",
        ),
        DependencyStatus.VERSION_CONFLICT: (
            "dependency_version",
            f"dependency {item_id} has no compatible installed version",
        ),
        DependencyStatus.KIND_UNKNOWN: (
            "dependency_kind_unknown",
            f"dependency {item_id} has no recorded component kind",
        ),
        DependencyStatus.KIND_CONFLICT: (
            "dependency_kind",
            f"dependency {item_id} has an incompatible component kind",
        ),
        DependencyStatus.SOURCE_AMBIGUOUS: (
            "dependency_source_ambiguous",
            f"dependency {item_id} is ambiguous across Marketplace sources",
        ),
        DependencyStatus.SELF_DEPENDENCY: (
            "self_dependency",
            f"component {resolution.required_by} depends on itself",
        ),
    }
    try:
        return specs[status]
    except KeyError as exc:
        raise ValueError(f"unsupported dependency status {status.value!r}") from exc


def _resolution(
    parent: RegistryItem,
    dependency: RegistryDependency,
    status: DependencyStatus,
    *,
    record: InstalledRegistryItem | None = None,
    candidate: RegistryItem | None = None,
    path: tuple[str, ...],
) -> DependencyResolution:
    return DependencyResolution(
        required_by=parent.item_id,
        item_id=dependency.item_id,
        item_kind=dependency.kind_value,
        optional=dependency.optional,
        minimum_version=dependency.version_range.minimum,
        maximum_version=dependency.version_range.maximum,
        status=status,
        installed_version=record.version if record is not None else None,
        candidate_version=candidate.version if candidate is not None else None,
        candidate_kind=candidate.kind if candidate is not None else None,
        candidate_source_registry=(candidate.source_registry if candidate is not None else None),
        path=path,
    )


def _finding(
    code: str,
    severity: FindingSeverity,
    message: str,
    resolution: DependencyResolution,
) -> ValidationFinding:
    details = [
        ("required_by", resolution.required_by),
        ("status", resolution.status.value),
    ]
    if resolution.item_kind is not None:
        details.append(("required_kind", resolution.item_kind))
    if resolution.installed_version is not None:
        details.append(("installed_version", resolution.installed_version))
    if resolution.candidate_version is not None:
        details.append(("candidate_version", resolution.candidate_version))
    if resolution.candidate_source_registry is not None:
        details.append(("candidate_source_registry", resolution.candidate_source_registry))
    return ValidationFinding(
        code,
        severity,
        message,
        FindingCategory.DEPENDENCY,
        resolution.item_id,
        tuple(details),
    )


def _environment_value_supported(
    supported: frozenset[str],
    current: str | None,
) -> bool:
    if not supported:
        return True
    if current is None:
        return False
    normalized = current.strip().casefold()
    return normalized in {value.strip().casefold() for value in supported}
