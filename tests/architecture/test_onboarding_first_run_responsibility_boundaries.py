from __future__ import annotations

from ai_multi_agent_platform.onboarding import OnboardingService
from ai_multi_agent_platform.onboarding.first_run_model_setup import build_model_setup_plan
from ai_multi_agent_platform.onboarding.first_run_resolution import resolve_first_run_path
from ai_multi_agent_platform.onboarding.first_run_service import (
    FirstRunPath,
    FirstRunPathProjection,
)
from ai_multi_agent_platform.onboarding.first_run_status import classify_first_run_state
from ai_multi_agent_platform.onboarding.first_run_types import (
    FirstRunPath as CanonicalFirstRunPath,
)
from ai_multi_agent_platform.onboarding.first_run_types import (
    FirstRunPathProjection as CanonicalFirstRunPathProjection,
)
from ai_multi_agent_platform.onboarding.service import (
    FirstRunPath as CompatibilityFirstRunPath,
)
from ai_multi_agent_platform.onboarding.service import (
    FirstRunPathProjection as CompatibilityFirstRunPathProjection,
)
from ai_multi_agent_platform.onboarding.service import (
    OnboardingService as CompatibilityOnboardingService,
)


def test_first_run_public_compatibility_imports_preserve_identity() -> None:
    assert CompatibilityOnboardingService is OnboardingService
    assert FirstRunPath is CanonicalFirstRunPath is CompatibilityFirstRunPath
    assert (
        FirstRunPathProjection
        is CanonicalFirstRunPathProjection
        is CompatibilityFirstRunPathProjection
    )


def test_first_run_responsibilities_have_focused_canonical_owners() -> None:
    assert OnboardingService.__module__.endswith(".onboarding.first_run_service")
    assert build_model_setup_plan.__module__.endswith(".onboarding.first_run_model_setup")
    assert classify_first_run_state.__module__.endswith(".onboarding.first_run_status")
    assert resolve_first_run_path.__module__.endswith(".onboarding.first_run_resolution")
    assert FirstRunPath.__module__.endswith(".onboarding.first_run_types")
