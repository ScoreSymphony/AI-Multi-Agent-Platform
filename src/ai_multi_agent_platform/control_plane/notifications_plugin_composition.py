"""Compose notifications on top of the current plugin + terminal Control Plane."""

from __future__ import annotations

from .notifications_live import ControlPlane as _NotificationControlPlane
from .notifications_live import ControlPlaneASGI, ControlPlaneHTTP, build_openapi
from .plugin_terminal_composition import ControlPlane as _PluginTerminalControlPlane


class ControlPlane(_NotificationControlPlane, _PluginTerminalControlPlane):
    """Current Control Plane with notifications, plugins, terminal and lower layers."""


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
