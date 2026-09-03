"""Compose optional plugin lifecycle on top of the current terminal Control Plane."""

from __future__ import annotations

from .plugin_api import ControlPlane as _PluginControlPlane
from .terminal_composition import (
    ControlPlane as _TerminalControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    build_openapi,
)


class ControlPlane(_PluginControlPlane, _TerminalControlPlane):
    """Current Control Plane with both optional plugin and terminal composition."""


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
