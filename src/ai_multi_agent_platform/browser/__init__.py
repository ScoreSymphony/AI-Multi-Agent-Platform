"""Replaceable browser/web capability boundary and self-hosted reference adapter."""

from .contracts import BrowserProvider
from .models import (
    BrowserNetworkPolicy,
    BrowserOperation,
    BrowserPrivacyClassification,
    BrowserProviderFeatures,
    BrowserSessionRef,
    BrowserSessionScope,
)
from .policy import BrowserNetworkPolicyHook, DefaultBrowserNetworkPolicyHook
from .reference import (
    BROWSER_CLOSE_SESSION_CAPABILITY_ID,
    BROWSER_DOWNLOAD_CAPABILITY_ID,
    BROWSER_EXTRACT_CAPABILITY_ID,
    BROWSER_FOLLOW_LINK_CAPABILITY_ID,
    BROWSER_NAVIGATE_CAPABILITY_ID,
    BROWSER_SUBMIT_FORM_CAPABILITY_ID,
    DefaultDownloadValidationHook,
    DownloadValidationHook,
    StdlibBrowserProvider,
)

__all__ = [
    "BROWSER_CLOSE_SESSION_CAPABILITY_ID",
    "BROWSER_DOWNLOAD_CAPABILITY_ID",
    "BROWSER_EXTRACT_CAPABILITY_ID",
    "BROWSER_FOLLOW_LINK_CAPABILITY_ID",
    "BROWSER_NAVIGATE_CAPABILITY_ID",
    "BROWSER_SUBMIT_FORM_CAPABILITY_ID",
    "BrowserNetworkPolicy",
    "BrowserNetworkPolicyHook",
    "BrowserOperation",
    "BrowserPrivacyClassification",
    "BrowserProvider",
    "BrowserProviderFeatures",
    "BrowserSessionRef",
    "BrowserSessionScope",
    "DefaultBrowserNetworkPolicyHook",
    "DefaultDownloadValidationHook",
    "DownloadValidationHook",
    "StdlibBrowserProvider",
]
