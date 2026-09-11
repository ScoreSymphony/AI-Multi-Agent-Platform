"""Deprecated compatibility shim for the historical ``cli.issue_214`` import.

Canonical authentication and Approval CLI code lives in :mod:`ai_multi_agent_platform.cli.auth`.
This module is deliberately excluded from runtime composition and exists only to preserve the
previous direct import during the v0 compatibility window. Remove it at the next breaking
package release after downstream import migration; do not add new callers.
"""

from .auth import main, run_cli

__all__ = ["main", "run_cli"]
