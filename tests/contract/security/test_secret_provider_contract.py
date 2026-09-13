"""Migrated under #722; original coverage tracked issue #34."""


# ruff: noqa: F401

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.configuration import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretAuditEvent,
    SecretProvider,
    SecretReference,
    redact_sensitive,
    redact_text,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def _reference() -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id="secret_issue34_completion",
        scope="project:project_demo",
        version="1",
    )


def test_secret_provider_contract_requires_audit_hook_boundary() -> None:
    assert "set_audit_hook" in SecretProvider.__abstractmethods__
