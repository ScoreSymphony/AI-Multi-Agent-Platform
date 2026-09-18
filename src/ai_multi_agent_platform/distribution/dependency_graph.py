"""Cross-kind Marketplace dependency graph and environment compatibility."""

from __future__ import annotations

from dataclasses import dataclass

from .decision_types import (
    CompatibilityDecision,
    DependencyResolution,
    DependencyStatus,
    InstallPlanStep,
)
from .items import InstalledRegistryItem, RegistryItem
from .models import RegistryDependency, VersionRange, version_key
from .validation import FindingCategory, FindingSeverity, ValidationContext, ValidationFinding


@dataclass(frozen=True, slots=True)
class _DependencyVisit:
    resolution: DependencyResolution
    next_dependencies: tuple[RegistryDependency, ...] | None = None


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
    context: ValidationContext | None = None,
) -> tuple[DependencyResolution, ...]:
    installed = {record.item_id: record for record in installed_items}
    candidate_overrides: dict[str, RegistryItem] = {}
    resolutions: list[DependencyResolution] = []

    for _attempt in range(max(2, len(catalog) + 1)):
        resolutions = []
        _walk_item(
            item,
            path=(item.item_id,),
            catalog=catalog,
            installed=installed,
            resolutions=resolutions,
            candidate_overrides=candidate_overrides,
            context=context,
        )
        resolved_overrides = _common_candidate_overrides(
            resolutions,
            catalog=catalog,
            installed=installed,
            context=context,
        )
        if resolved_overrides == candidate_overrides:
            break
        candidate_overrides = resolved_overrides

    _append_constraint_conflicts(
        item,
        resolutions=resolutions,
        catalog=catalog,
        installed=installed,
        context=context,
    )
    return tuple(resolutions)


def _walk_item(
    parent: RegistryItem,
    *,
    path: tuple[str, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    resolutions: list[DependencyResolution],
    candidate_overrides: dict[str, RegistryItem],
    context: ValidationContext | None,
) -> None:
    _walk_dependencies(
        parent.item_id,
        parent.dependencies,
        path=path,
        catalog=catalog,
        installed=installed,
        resolutions=resolutions,
        candidate_overrides=candidate_overrides,
        context=context,
    )


def _walk_dependencies(
    parent_id: str,
    dependencies: tuple[RegistryDependency, ...],
    *,
    path: tuple[str, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    resolutions: list[DependencyResolution],
    candidate_overrides: dict[str, RegistryItem],
    context: ValidationContext | None,
) -> None:
    for dependency in dependencies:
        visit = _resolve_dependency(
            parent_id,
            dependency,
            path=path,
            catalog=catalog,
            installed=installed,
            candidate_overrides=candidate_overrides,
            context=context,
        )
        resolutions.append(visit.resolution)
        if dependency.optional or visit.next_dependencies is None:
            continue
        _walk_dependencies(
            dependency.item_id,
            visit.next_dependencies,
            path=(*path, dependency.item_id),
            catalog=catalog,
            installed=installed,
            resolutions=resolutions,
            candidate_overrides=candidate_overrides,
            context=context,
        )


def _resolve_dependency(
    parent_id: str,
    dependency: RegistryDependency,
    *,
    path: tuple[str, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    candidate_overrides: dict[str, RegistryItem],
    context: ValidationContext | None,
) -> _DependencyVisit:
    dependency_path = (*path, dependency.item_id)
    if dependency.item_id == parent_id:
        return _DependencyVisit(
            _resolution(
                parent_id,
                dependency,
                DependencyStatus.SELF_DEPENDENCY,
                path=dependency_path,
            )
        )
    if dependency.item_id in path:
        return _DependencyVisit(
            _resolution(
                parent_id,
                dependency,
                DependencyStatus.CYCLE,
                path=dependency_path,
            )
        )

    record = installed.get(dependency.item_id)
    if record is not None:
        return _installed_visit(
            parent_id,
            dependency,
            record=record,
            candidate=_installed_catalog_candidate(dependency, catalog, record),
            path=dependency_path,
        )

    override = candidate_overrides.get(dependency.item_id)
    if override is not None:
        required_kind = dependency.kind_value
        if required_kind is not None and override.kind != required_kind:
            return _DependencyVisit(
                _resolution(
                    parent_id,
                    dependency,
                    DependencyStatus.KIND_CONFLICT,
                    path=dependency_path,
                )
            )
        if not dependency.version_range.contains(override.version):
            return _DependencyVisit(
                _resolution(
                    parent_id,
                    dependency,
                    DependencyStatus.VERSION_CONFLICT,
                    path=dependency_path,
                )
            )
        compatibility = evaluate_compatibility(override, context) if context is not None else None
        status = (
            DependencyStatus.ENVIRONMENT_INCOMPATIBLE
            if compatibility is not None and not compatibility.compatible
            else DependencyStatus.AVAILABLE
        )
        return _DependencyVisit(
            _resolution(
                parent_id,
                dependency,
                status,
                candidate=override,
                candidate_compatibility=compatibility,
                path=dependency_path,
            ),
            override.dependencies if status is DependencyStatus.AVAILABLE else None,
        )

    candidate, catalog_status, candidate_compatibility = _select_dependency_candidate(
        dependency,
        catalog,
        context=context,
    )
    status = catalog_status
    if dependency.optional and status is DependencyStatus.MISSING:
        status = DependencyStatus.OPTIONAL_MISSING
    return _DependencyVisit(
        _resolution(
            parent_id,
            dependency,
            status,
            candidate=candidate,
            candidate_compatibility=candidate_compatibility,
            path=dependency_path,
        ),
        candidate.dependencies
        if status is DependencyStatus.AVAILABLE and candidate is not None
        else None,
    )


def _installed_visit(
    parent_id: str,
    dependency: RegistryDependency,
    *,
    record: InstalledRegistryItem,
    candidate: RegistryItem | None,
    path: tuple[str, ...],
) -> _DependencyVisit:
    status = _installed_dependency_status(dependency, record)
    next_dependencies = None
    if status is DependencyStatus.SATISFIED:
        if record.dependencies is not None:
            next_dependencies = record.dependencies
        elif candidate is not None:
            next_dependencies = candidate.dependencies
    return _DependencyVisit(
        _resolution(
            parent_id,
            dependency,
            status,
            record=record,
            candidate=candidate,
            path=path,
        ),
        next_dependencies,
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


def _installed_catalog_candidate(
    dependency: RegistryDependency,
    catalog: tuple[RegistryItem, ...],
    record: InstalledRegistryItem,
) -> RegistryItem | None:
    return next(
        (
            candidate
            for candidate in catalog
            if candidate.item_id == dependency.item_id
            and candidate.version == record.version
            and candidate.source_registry == record.source_registry
            and (dependency.kind_value is None or candidate.kind == dependency.kind_value)
        ),
        None,
    )


def _select_dependency_candidate(
    dependency: RegistryDependency,
    catalog: tuple[RegistryItem, ...],
    *,
    context: ValidationContext | None,
) -> tuple[RegistryItem | None, DependencyStatus, CompatibilityDecision | None]:
    by_id = tuple(candidate for candidate in catalog if candidate.item_id == dependency.item_id)
    if not by_id:
        return None, DependencyStatus.MISSING, None

    by_kind = _matching_kind_candidates(dependency, by_id)
    if not by_kind:
        return None, DependencyStatus.KIND_CONFLICT, None

    version_compatible = tuple(
        candidate
        for candidate in by_kind
        if dependency.version_range.contains(candidate.version) and not candidate.yanked
    )
    if not version_compatible:
        return None, DependencyStatus.VERSION_CONFLICT, None
    if len({candidate.source_registry for candidate in version_compatible}) > 1:
        return None, DependencyStatus.SOURCE_AMBIGUOUS, None

    if context is None:
        candidate = max(version_compatible, key=lambda item: version_key(item.version))
        return candidate, DependencyStatus.AVAILABLE, None

    evaluated = tuple(
        (candidate, evaluate_compatibility(candidate, context)) for candidate in version_compatible
    )
    installable = tuple(pair for pair in evaluated if pair[1].compatible)
    if installable:
        candidate, compatibility = max(
            installable,
            key=lambda pair: version_key(pair[0].version),
        )
        return candidate, DependencyStatus.AVAILABLE, compatibility

    candidate, compatibility = max(
        evaluated,
        key=lambda pair: version_key(pair[0].version),
    )
    return candidate, DependencyStatus.ENVIRONMENT_INCOMPATIBLE, compatibility


def _matching_kind_candidates(
    dependency: RegistryDependency,
    candidates: tuple[RegistryItem, ...],
) -> tuple[RegistryItem, ...]:
    if dependency.kind_value is None:
        return candidates
    return tuple(candidate for candidate in candidates if candidate.kind == dependency.kind_value)


def _common_candidate_overrides(
    resolutions: list[DependencyResolution],
    *,
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    context: ValidationContext | None,
) -> dict[str, RegistryItem]:
    overrides: dict[str, RegistryItem] = {}
    for item_id, requirements in _group_required_resolutions(resolutions).items():
        candidate = _common_candidate_override(
            item_id,
            requirements=tuple(requirements),
            catalog=catalog,
            installed=installed,
            context=context,
        )
        if candidate is not None:
            overrides[item_id] = candidate
    return overrides


def _common_candidate_override(
    item_id: str,
    *,
    requirements: tuple[DependencyResolution, ...],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    context: ValidationContext | None,
) -> RegistryItem | None:
    if len(requirements) < 2 or item_id in installed:
        return None
    kind_consistent, required_kind = _common_required_kind(requirements)
    if not kind_consistent:
        return None
    version_candidates = _common_version_candidates(
        item_id,
        requirements=requirements,
        required_kind=required_kind,
        catalog=catalog,
    )
    if not version_candidates or _has_multiple_sources(version_candidates):
        return None
    installable = tuple(
        candidate
        for candidate in version_candidates
        if context is None or evaluate_compatibility(candidate, context).compatible
    )
    if not installable:
        return None
    return max(installable, key=lambda candidate: version_key(candidate.version))


def _common_required_kind(
    requirements: tuple[DependencyResolution, ...],
) -> tuple[bool, str | None]:
    kinds = {
        requirement.item_kind for requirement in requirements if requirement.item_kind is not None
    }
    if len(kinds) > 1:
        return False, None
    return True, next(iter(kinds), None)


def _common_version_candidates(
    item_id: str,
    *,
    requirements: tuple[DependencyResolution, ...],
    required_kind: str | None,
    catalog: tuple[RegistryItem, ...],
) -> tuple[RegistryItem, ...]:
    ranges = _constraint_ranges(requirements)
    return tuple(
        candidate
        for candidate in catalog
        if candidate.item_id == item_id
        and not candidate.yanked
        and (required_kind is None or candidate.kind == required_kind)
        and all(version_range.contains(candidate.version) for version_range in ranges)
    )


def _has_multiple_sources(candidates: tuple[RegistryItem, ...]) -> bool:
    return len({candidate.source_registry for candidate in candidates}) > 1


def _group_required_resolutions(
    resolutions: list[DependencyResolution],
) -> dict[str, list[DependencyResolution]]:
    grouped: dict[str, list[DependencyResolution]] = {}
    for resolution in resolutions:
        if not resolution.optional:
            grouped.setdefault(resolution.item_id, []).append(resolution)
    return grouped


def _append_constraint_conflicts(
    root: RegistryItem,
    *,
    resolutions: list[DependencyResolution],
    catalog: tuple[RegistryItem, ...],
    installed: dict[str, InstalledRegistryItem],
    context: ValidationContext | None,
) -> None:
    for item_id, requirements in _group_required_resolutions(resolutions).items():
        conflict = _constraint_conflict(
            root,
            item_id=item_id,
            requirements=tuple(requirements),
            catalog=catalog,
            installed=installed,
            context=context,
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
    context: ValidationContext | None,
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
    if _already_has_terminal_graph_error(requirements):
        return None

    common_candidates = _common_version_candidates(
        item_id,
        requirements=requirements,
        required_kind=required_kind,
        catalog=catalog,
    )
    if common_candidates:
        environment_conflict = _common_environment_conflict(
            root,
            item_id=item_id,
            required_kind=required_kind,
            ranges=ranges,
            candidates=common_candidates,
            context=context,
        )
        return environment_conflict

    return _aggregate_conflict(
        root,
        item_id,
        DependencyStatus.VERSION_CONFLICT,
        item_kind=required_kind,
        minimum_version=_intersection_minimum(ranges),
        maximum_version=_intersection_maximum(ranges),
        installed_version=record.version if record is not None else None,
    )


def _common_environment_conflict(
    root: RegistryItem,
    *,
    item_id: str,
    required_kind: str | None,
    ranges: tuple[VersionRange, ...],
    candidates: tuple[RegistryItem, ...],
    context: ValidationContext | None,
) -> DependencyResolution | None:
    if context is None:
        return None
    evaluated = tuple(
        (candidate, evaluate_compatibility(candidate, context)) for candidate in candidates
    )
    if any(compatibility.compatible for _candidate, compatibility in evaluated):
        return None
    candidate, compatibility = max(
        evaluated,
        key=lambda pair: version_key(pair[0].version),
    )
    return _aggregate_conflict(
        root,
        item_id,
        DependencyStatus.ENVIRONMENT_INCOMPATIBLE,
        item_kind=required_kind,
        minimum_version=_intersection_minimum(ranges),
        maximum_version=_intersection_maximum(ranges),
        candidate=candidate,
        candidate_compatibility=compatibility,
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


def _already_has_terminal_graph_error(
    requirements: tuple[DependencyResolution, ...],
) -> bool:
    terminal = {
        DependencyStatus.MISSING,
        DependencyStatus.SOURCE_AMBIGUOUS,
        DependencyStatus.ENVIRONMENT_INCOMPATIBLE,
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
    candidate: RegistryItem | None = None,
    candidate_compatibility: CompatibilityDecision | None = None,
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
        candidate_version=candidate.version if candidate is not None else None,
        candidate_kind=candidate.kind if candidate is not None else None,
        candidate_source_registry=(candidate.source_registry if candidate is not None else None),
        candidate_compatibility=candidate_compatibility,
        path=(root.item_id, item_id),
    )


def _intersection_minimum(ranges: tuple[VersionRange, ...]) -> str | None:
    values = tuple(version_range.minimum for version_range in ranges if version_range.minimum)
    return max(values, key=version_key) if values else None


def _intersection_maximum(ranges: tuple[VersionRange, ...]) -> str | None:
    values = tuple(version_range.maximum for version_range in ranges if version_range.maximum)
    return min(values, key=version_key) if values else None


def deterministic_install_order(
    root: RegistryItem,
    resolutions: tuple[DependencyResolution, ...],
) -> tuple[InstallPlanStep, ...]:
    """Return a leaf-first plan only when every hard dependency is safely resolvable."""

    unsafe = {
        DependencyStatus.MISSING,
        DependencyStatus.VERSION_CONFLICT,
        DependencyStatus.KIND_UNKNOWN,
        DependencyStatus.KIND_CONFLICT,
        DependencyStatus.SOURCE_AMBIGUOUS,
        DependencyStatus.ENVIRONMENT_INCOMPATIBLE,
        DependencyStatus.SELF_DEPENDENCY,
        DependencyStatus.CYCLE,
        DependencyStatus.REQUIRED_BY_INSTALLED,
        DependencyStatus.UNKNOWN_INSTALLED_DEPENDENT,
    }
    if any(not resolution.optional and resolution.status in unsafe for resolution in resolutions):
        return ()

    selected_candidates: dict[str, tuple[str, str | None]] = {}
    for resolution in resolutions:
        if resolution.optional or resolution.status is not DependencyStatus.AVAILABLE:
            continue
        if resolution.candidate_version is None:
            return ()
        identity = (resolution.candidate_version, resolution.candidate_source_registry)
        previous = selected_candidates.setdefault(resolution.item_id, identity)
        if previous != identity:
            return ()

    ordered: list[InstallPlanStep] = []
    seen: set[tuple[str, str, str | None]] = set()
    for resolution in reversed(resolutions):
        if resolution.optional or resolution.status is not DependencyStatus.AVAILABLE:
            continue
        if resolution.candidate_version is None or resolution.candidate_kind is None:
            return ()
        key = (
            resolution.item_id,
            resolution.candidate_version,
            resolution.candidate_source_registry,
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(
            InstallPlanStep(
                item_id=resolution.item_id,
                item_kind=resolution.candidate_kind,
                version=resolution.candidate_version,
                source_registry=resolution.candidate_source_registry,
            )
        )

    ordered.append(
        InstallPlanStep(
            item_id=root.item_id,
            item_kind=root.kind,
            version=root.version,
            source_registry=root.source_registry,
        )
    )
    return tuple(ordered)


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
        DependencyStatus.ENVIRONMENT_INCOMPATIBLE: (
            "dependency_environment_incompatible",
            f"dependency {item_id} is not installable in the current environment",
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
    parent_id: str,
    dependency: RegistryDependency,
    status: DependencyStatus,
    *,
    record: InstalledRegistryItem | None = None,
    candidate: RegistryItem | None = None,
    candidate_compatibility: CompatibilityDecision | None = None,
    path: tuple[str, ...],
) -> DependencyResolution:
    return DependencyResolution(
        required_by=parent_id,
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
        candidate_compatibility=candidate_compatibility,
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
    details.extend(_compatibility_finding_details(resolution.candidate_compatibility))
    return ValidationFinding(
        code,
        severity,
        message,
        FindingCategory.DEPENDENCY,
        resolution.item_id,
        tuple(details),
    )


def _compatibility_finding_details(
    compatibility: CompatibilityDecision | None,
) -> list[tuple[str, str]]:
    if compatibility is None:
        return []
    details: list[tuple[str, str]] = []
    if not compatibility.platform_compatible:
        details.append(("platform_compatible", "false"))
    if not compatibility.operating_system_compatible:
        details.append(("operating_system_compatible", "false"))
    if not compatibility.architecture_compatible:
        details.append(("architecture_compatible", "false"))
    if compatibility.missing_runtimes:
        details.append(("missing_runtimes", ", ".join(compatibility.missing_runtimes)))
    if compatibility.missing_capabilities:
        details.append(("missing_capabilities", ", ".join(compatibility.missing_capabilities)))
    if compatibility.missing_plugins:
        details.append(("missing_plugins", ", ".join(compatibility.missing_plugins)))
    if compatibility.missing_connectors:
        details.append(("missing_connectors", ", ".join(compatibility.missing_connectors)))
    if compatibility.missing_models:
        details.append(("missing_models", ", ".join(compatibility.missing_models)))
    return details


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
