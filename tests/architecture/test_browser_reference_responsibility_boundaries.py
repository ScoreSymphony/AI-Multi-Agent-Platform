from __future__ import annotations

from ai_multi_agent_platform.browser import (
    BROWSER_NAVIGATE_CAPABILITY_ID,
    DefaultDownloadValidationHook,
    DownloadValidationHook,
    StdlibBrowserProvider,
)
from ai_multi_agent_platform.browser.reference import (
    BROWSER_NAVIGATE_CAPABILITY_ID as ReferenceNavigateCapabilityId,
)
from ai_multi_agent_platform.browser.reference import (
    DefaultDownloadValidationHook as ReferenceDownloadValidationHook,
)
from ai_multi_agent_platform.browser.reference import (
    DownloadValidationHook as ReferenceDownloadValidationProtocol,
)
from ai_multi_agent_platform.browser.reference import (
    StdlibBrowserProvider as ReferenceStdlibBrowserProvider,
)
from ai_multi_agent_platform.browser.reference_capabilities import browser_capability_registrations
from ai_multi_agent_platform.browser.reference_http import ReferenceBrowserTransport
from ai_multi_agent_platform.browser.reference_page import PageParser


def test_browser_reference_public_compatibility_imports_preserve_identity() -> None:
    assert StdlibBrowserProvider is ReferenceStdlibBrowserProvider
    assert DefaultDownloadValidationHook is ReferenceDownloadValidationHook
    assert DownloadValidationHook is ReferenceDownloadValidationProtocol
    assert BROWSER_NAVIGATE_CAPABILITY_ID == ReferenceNavigateCapabilityId


def test_browser_reference_responsibilities_have_focused_canonical_owners() -> None:
    assert StdlibBrowserProvider.__module__.endswith(".browser.reference")
    assert DefaultDownloadValidationHook.__module__.endswith(".browser.reference")
    assert browser_capability_registrations.__module__.endswith(".browser.reference_capabilities")
    assert ReferenceBrowserTransport.__module__.endswith(".browser.reference_http")
    assert PageParser.__module__.endswith(".browser.reference_page")
