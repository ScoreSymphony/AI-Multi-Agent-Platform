from __future__ import annotations

import pytest

from ai_multi_agent_platform.adapters import hermes
from ai_multi_agent_platform.adapters.hermes_config import (
    HERMES_CONFIGURATION_SCHEMA,
    HermesAdapterConfig,
)
from ai_multi_agent_platform.adapters.hermes_http import (
    HermesHttpResponse,
    HermesHttpTransport,
    UrllibHermesHttpTransport,
)
from ai_multi_agent_platform.adapters.hermes_mapping import HermesAgentMapper


@pytest.mark.unit
def test_hermes_public_facade_preserves_extracted_adapter_types() -> None:
    assert hermes.HERMES_CONFIGURATION_SCHEMA is HERMES_CONFIGURATION_SCHEMA
    assert hermes.HermesAdapterConfig is HermesAdapterConfig
    assert hermes.HermesHttpResponse is HermesHttpResponse
    assert hermes.HermesHttpTransport is HermesHttpTransport
    assert hermes.UrllibHermesHttpTransport is UrllibHermesHttpTransport
    assert hermes.HermesAgentMapper is HermesAgentMapper


@pytest.mark.unit
def test_hermes_extracted_modules_have_single_responsibility_import_direction() -> None:
    assert HermesAdapterConfig.__module__.endswith(".hermes_config")
    assert HermesHttpResponse.__module__.endswith(".hermes_http")
    assert UrllibHermesHttpTransport.__module__.endswith(".hermes_http")
    assert HermesAgentMapper.__module__.endswith(".hermes_mapping")
    assert hermes.HermesOrchestrator.__module__.endswith(".hermes")
