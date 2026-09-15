"""Compatibility import for explicit Organization/Accounting composition (#982).

Canonical ownership and integration behavior live in
``organization_explicit_composition``. The historical module path remains importable
without retaining a second registration or command-dispatch implementation.
"""

from .organization_explicit_composition import (
    ACCOUNTING_MODULE,
    ORGANIZATION_MODULE,
    ORGANIZATION_RUNTIME_COMMANDS,
    ControlPlane,
)

__all__ = [
    "ACCOUNTING_MODULE",
    "ControlPlane",
    "ORGANIZATION_MODULE",
    "ORGANIZATION_RUNTIME_COMMANDS",
]
