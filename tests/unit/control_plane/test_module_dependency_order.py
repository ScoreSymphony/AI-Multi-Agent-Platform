from __future__ import annotations

from typing import Any

import pytest

from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ControlPlaneModule
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _control_plane() -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(kernel=kernel, events=repository)


def test_module_dependencies_order_contributors_before_lexical_name_order() -> None:
    calls: list[str] = []

    def dependency(specification: dict[str, Any]) -> None:
        del specification
        calls.append("zeta")

    def dependent(specification: dict[str, Any]) -> None:
        del specification
        calls.append("alpha")

    control_plane = _control_plane()
    control_plane.register_modules(
        (
            ControlPlaneModule(
                name="alpha",
                openapi_contributors=(dependent,),
                requires=frozenset({"zeta"}),
            ),
            ControlPlaneModule(
                name="zeta",
                openapi_contributors=(dependency,),
            ),
        )
    )

    control_plane.apply_openapi_contributions({})

    assert calls == ["zeta", "alpha"]
    assert control_plane.registered_modules == ("alpha", "zeta")


def test_module_dependency_cycle_is_rejected_before_registry_mutation() -> None:
    control_plane = _control_plane()

    with pytest.raises(ValueError, match="module dependency cycle"):
        control_plane.register_modules(
            (
                ControlPlaneModule(name="alpha", requires=frozenset({"beta"})),
                ControlPlaneModule(name="beta", requires=frozenset({"alpha"})),
            )
        )

    assert control_plane.registered_modules == ()
    assert control_plane.registered_collections == ()
    assert control_plane.registered_commands == ()
