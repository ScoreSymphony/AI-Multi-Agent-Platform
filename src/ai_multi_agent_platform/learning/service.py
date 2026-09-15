"""Compatibility imports for governed learning services.

Canonical implementations live in the focused learning owner modules:
:mod:`ai_multi_agent_platform.learning.quality_gate` for evidence validation and
:mod:`ai_multi_agent_platform.learning.governed_learning_workflow` for workflow coordination.
New production imports must use those modules or the supported package-level exports.
"""

from .governed_learning_workflow import LearningService as LearningService
from .governed_learning_workflow import _promotion_action as _promotion_action
from .quality_gate import LearningQualityGate as LearningQualityGate

__all__ = ["LearningQualityGate", "LearningService"]
