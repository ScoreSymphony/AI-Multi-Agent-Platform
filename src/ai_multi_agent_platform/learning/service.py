"""Compatibility imports for the governed learning workflow.

Canonical implementation lives in
:mod:`ai_multi_agent_platform.learning.governed_learning_workflow`.
New production imports must use that module or the supported package-level exports.
"""

from .governed_learning_workflow import (
    LearningQualityGate as LearningQualityGate,
    LearningService as LearningService,
    _promotion_action as _promotion_action,
)

__all__ = ["LearningQualityGate", "LearningService"]
