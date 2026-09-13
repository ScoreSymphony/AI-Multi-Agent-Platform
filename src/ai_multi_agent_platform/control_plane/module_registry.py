"""Deterministic installation of explicit Control Plane modules.

The installer deliberately targets the registration state owned by
``control_plane.extensions.ControlPlane`` instead of dispatching through legacy
subclass overrides.  This lets domains migrate away from MRO composition one at a
time without making installation order or an intermediate compatibility facade the
owner of a route or command.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .extensions import (
    ControlPlane,
    ControlPlaneModule,
    _route_key,
    _validate_command_name,
    _validate_extension_collection,
)


def install_control_plane_modules(
    control_plane: ControlPlane,
    modules: Sequence[ControlPlaneModule],
) -> None:
    """Atomically validate and install a batch of explicit domain modules.

    Dependencies may be satisfied by any module in the same batch, so caller order
    is irrelevant.  Resource, command and special-route ownership is claimed before
    any mutation occurs.  Installation then uses the stable extension registry
    directly; legacy subclass guards therefore cannot reintroduce MRO ordering into
    the new composition path.
    """

    if not modules:
        return

    by_name: dict[str, ControlPlaneModule] = {}
    for module in modules:
        if module.name in by_name or module.name in control_plane._registered_modules:
            raise ValueError(f"duplicate Control Plane module ownership: {module.name!r}")
        by_name[module.name] = module

    available = set(control_plane._registered_modules) | set(by_name)
    for module in by_name.values():
        missing = sorted(module.requires.difference(available))
        if missing:
            raise ValueError(
                f"Control Plane module {module.name!r} requires missing modules: {missing!r}"
            )

    resource_claims = dict(control_plane._resource_owners)
    command_claims = dict(control_plane._command_owners)
    route_claims = dict(control_plane._route_owners)
    for name in sorted(by_name):
        module = by_name[name]
        for collection in sorted(module.resource_services):
            _validate_extension_collection(collection)
            _claim(resource_claims, collection, name, kind="resource")
        for command in sorted(module.command_handlers):
            _validate_command_name(command)
            _claim(command_claims, command, name, kind="command")
        for route in module.routes:
            _claim(route_claims, _route_key(route.method, route.path), name, kind="route")

    for name in sorted(by_name):
        module = by_name[name]
        for collection, service in sorted(module.resource_services.items()):
            # Explicit base-class dispatch is intentional: migrated module ownership
            # must not depend on which legacy compatibility subclass happens to wrap
            # the canonical registry during the transition.
            ControlPlane.register_resource_service(
                control_plane,
                collection,
                service,
                owner=name,
            )
        for command, handler in sorted(module.command_handlers.items()):
            ControlPlane.register_command(
                control_plane,
                command,
                handler,
                owner=name,
            )
        for route in sorted(module.routes, key=lambda item: (item.method, item.path)):
            key = _route_key(route.method, route.path)
            control_plane._route_handlers[key] = route.handler
            control_plane._route_owners[key] = name
        for contributor in module.openapi_contributors:
            control_plane._openapi_contributors.append((name, contributor))
        control_plane._registered_modules[name] = module

    control_plane._openapi_contributors.sort(key=lambda item: item[0])


def _claim(claims: dict[Any, str], key: Any, owner: str, *, kind: str) -> None:
    existing_owner = claims.get(key)
    if existing_owner is not None:
        raise ValueError(
            f"duplicate Control Plane {kind} ownership for {key!r}: "
            f"{existing_owner!r} and {owner!r}"
        )
    claims[key] = owner


__all__ = ["install_control_plane_modules"]
