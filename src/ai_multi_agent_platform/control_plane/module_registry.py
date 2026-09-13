"""Deterministic installation of explicit Control Plane modules.

This helper exists for migration call sites that should not dispatch through a legacy
compatibility subclass.  Validation and ownership remain implemented exactly once by
``extensions.ControlPlane.register_modules``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .extensions import ControlPlane, ControlPlaneModule


def install_control_plane_modules(
    control_plane: ControlPlane,
    modules: Sequence[ControlPlaneModule],
) -> None:
    """Install modules through the canonical registry, bypassing legacy overrides."""

    ControlPlane.register_modules(control_plane, modules)


__all__ = ["install_control_plane_modules"]
