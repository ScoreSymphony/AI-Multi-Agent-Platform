from __future__ import annotations

import pytest

from ai_multi_agent_platform import learning
from ai_multi_agent_platform.learning import governed_learning_workflow, quality_gate
from ai_multi_agent_platform.learning import service as compatibility_service


@pytest.mark.unit
def test_learning_public_exports_preserve_quality_gate_and_service_identity() -> None:
    assert learning.LearningQualityGate is quality_gate.LearningQualityGate
    assert governed_learning_workflow.LearningQualityGate is quality_gate.LearningQualityGate
    assert compatibility_service.LearningQualityGate is quality_gate.LearningQualityGate
    assert learning.LearningService is governed_learning_workflow.LearningService
    assert compatibility_service.LearningService is governed_learning_workflow.LearningService


@pytest.mark.unit
def test_learning_quality_gate_and_workflow_have_distinct_canonical_owners() -> None:
    assert quality_gate.LearningQualityGate.__module__.endswith(".quality_gate")
    assert governed_learning_workflow.LearningService.__module__.endswith(
        ".governed_learning_workflow"
    )
