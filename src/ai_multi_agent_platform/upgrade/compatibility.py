"""Cross-release plugin, adapter, portable-format and template compatibility."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ai_multi_agent_platform.plugins.models import (
    PLUGIN_MANIFEST_VERSION,
    PluginManifest,
)

from .models import CheckSeverity, PreflightCheck

FormatTranslator = Callable[[object], object]


class CompatibilityError(RuntimeError):
    """Raised when no explicit supported compatibility path exists."""


@dataclass(frozen=True, slots=True)
class ExtensionCompatibilitySpec:
    extension_id: str
    installed_version: str
    required: bool
    supported_platform_min: str | None = None
    supported_platform_max: str | None = None
    actual_interfaces: Mapping[str, str] = field(default_factory=dict)
    required_interfaces: Mapping[str, str] = field(default_factory=dict)


class FormatTranslatorRegistry:
    """Explicit directed translators; unsupported formats are never guessed or reinterpreted."""

    def __init__(self, current_version: str) -> None:
        self.current_version = current_version
        self._edges: dict[tuple[str, str], FormatTranslator] = {}

    def register(self, from_version: str, to_version: str, translator: FormatTranslator) -> None:
        key = (from_version, to_version)
        if key in self._edges:
            raise ValueError(f"duplicate format translator {from_version!r} -> {to_version!r}")
        if from_version == to_version:
            raise ValueError("format translator must change the version")
        self._edges[key] = translator

    def can_translate(self, from_version: str, to_version: str | None = None) -> bool:
        target = to_version or self.current_version
        try:
            self.path(from_version, target)
        except CompatibilityError:
            return False
        return True

    def path(self, from_version: str, to_version: str | None = None) -> tuple[str, ...]:
        target = to_version or self.current_version
        if from_version == target:
            return (from_version,)
        frontier: list[tuple[str, tuple[str, ...]]] = [(from_version, (from_version,))]
        seen = {from_version}
        while frontier:
            version, path = frontier.pop(0)
            next_versions = sorted(
                destination for source, destination in self._edges if source == version
            )
            for candidate in next_versions:
                if candidate in seen:
                    continue
                candidate_path = (*path, candidate)
                if candidate == target:
                    return candidate_path
                seen.add(candidate)
                frontier.append((candidate, candidate_path))
        raise CompatibilityError(
            f"unsupported format version {from_version!r}; no translator to {target!r}"
        )

    def translate(
        self,
        payload: object,
        from_version: str,
        to_version: str | None = None,
    ) -> object:
        path = self.path(from_version, to_version)
        translated = payload
        for index in range(len(path) - 1):
            translated = self._edges[(path[index], path[index + 1])](translated)
        return translated


def plugin_compatibility_checks(
    manifests: tuple[PluginManifest, ...],
    *,
    target_platform: str,
    expected_interfaces: Mapping[str, str] | None = None,
    required_plugin_ids: frozenset[str] = frozenset(),
) -> tuple[PreflightCheck, ...]:
    expected = expected_interfaces or {}
    checks: list[PreflightCheck] = []
    for manifest in manifests:
        required = manifest.plugin_id in required_plugin_ids
        reasons: list[str] = []
        if manifest.manifest_version != PLUGIN_MANIFEST_VERSION:
            reasons.append(
                f"plugin manifest version {manifest.manifest_version!r} != "
                f"supported {PLUGIN_MANIFEST_VERSION!r}"
            )
        if not manifest.supported_platform.contains(target_platform):
            reasons.append(f"platform {target_platform} is outside declared supported range")
        for extension in manifest.extensions:
            expected_version = expected.get(extension.extension_id)
            if expected_version is not None and extension.interface_version != expected_version:
                reasons.append(
                    f"extension {extension.extension_id} interface "
                    f"{extension.interface_version} != {expected_version}"
                )
        if reasons:
            checks.append(
                PreflightCheck(
                    code="plugin.incompatible",
                    severity=CheckSeverity.ERROR if required else CheckSeverity.WARNING,
                    message=(
                        f"required plugin {manifest.plugin_id} is incompatible"
                        if required
                        else f"optional plugin {manifest.plugin_id} must remain disabled"
                    ),
                    details={
                        "plugin_id": manifest.plugin_id,
                        "plugin_version": manifest.plugin_version,
                        "manifest_version": manifest.manifest_version,
                        "reasons": reasons,
                    },
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    code="plugin.compatible",
                    severity=CheckSeverity.INFO,
                    message=f"plugin {manifest.plugin_id} is compatible",
                    details={
                        "plugin_version": manifest.plugin_version,
                        "manifest_version": manifest.manifest_version,
                    },
                )
            )
    return tuple(checks)


def extension_compatibility_checks(
    specs: tuple[ExtensionCompatibilitySpec, ...],
    *,
    target_platform: str,
) -> tuple[PreflightCheck, ...]:
    checks: list[PreflightCheck] = []
    for spec in specs:
        reasons: list[str] = []
        if spec.supported_platform_min is not None and _version_key(target_platform) < _version_key(
            spec.supported_platform_min
        ):
            reasons.append(f"requires platform >= {spec.supported_platform_min}")
        if spec.supported_platform_max is not None and _version_key(target_platform) > _version_key(
            spec.supported_platform_max
        ):
            reasons.append(f"requires platform <= {spec.supported_platform_max}")
        for interface_id, required_version in spec.required_interfaces.items():
            actual_version = spec.actual_interfaces.get(interface_id)
            if actual_version != required_version:
                reasons.append(
                    f"interface {interface_id} is {actual_version!r}, requires {required_version!r}"
                )
        if reasons:
            checks.append(
                PreflightCheck(
                    code="adapter.incompatible",
                    severity=CheckSeverity.ERROR if spec.required else CheckSeverity.WARNING,
                    message=(
                        f"required adapter {spec.extension_id} is incompatible"
                        if spec.required
                        else f"optional adapter {spec.extension_id} must remain disabled"
                    ),
                    details={
                        "adapter_id": spec.extension_id,
                        "adapter_version": spec.installed_version,
                        "reasons": reasons,
                    },
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    code="adapter.compatible",
                    severity=CheckSeverity.INFO,
                    message=f"adapter {spec.extension_id} is compatible",
                    details={"adapter_version": spec.installed_version},
                )
            )
    return tuple(checks)


def format_compatibility_check(
    *,
    kind: str,
    source_version: str,
    target_version: str,
    translators: FormatTranslatorRegistry,
) -> PreflightCheck:
    if translators.can_translate(source_version, target_version):
        return PreflightCheck(
            code=f"{kind}.compatible",
            severity=CheckSeverity.INFO,
            message=f"{kind} format {source_version} can be interpreted as {target_version}",
            details={"translation_path": list(translators.path(source_version, target_version))},
        )
    return PreflightCheck(
        code=f"{kind}.unsupported",
        severity=CheckSeverity.ERROR,
        message=f"unsupported {kind} format {source_version}; target is {target_version}",
    )


def _version_key(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise CompatibilityError(f"platform version {value!r} must be numeric dotted version")
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (3 - len(numbers)))
    return numbers[0], numbers[1], numbers[2]
