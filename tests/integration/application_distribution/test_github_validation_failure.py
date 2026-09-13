from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    GITHUB_RELEASE_CONNECTOR_TYPE,
    GITHUB_RELEASE_CONNECTOR_VERSION,
    Connection,
    GitHubReleaseConnectorProvider,
    GitHubRestResponse,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference

_TOKEN = "issue751-validation-credential-DO-NOT-LEAK"


class _UnavailableValidationTransport:
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body=None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> GitHubRestResponse:
        del json_body, data, content_type
        assert method == "GET"
        assert url == "https://api.github.com/user"
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"
        return GitHubRestResponse(status=503, body={"message": _TOKEN})


def test_connection_validation_provider_failure_is_retryable_and_credential_safe() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        secrets = LocalSecretProvider()
        reference = SecretReference(
            provider="local-secrets",
            secret_id="issue751-validation-token",
            scope=project_id,
        )
        await secrets.create(
            reference,
            _TOKEN,
            purpose="github-api-token",
            allowed_consumers=("connector.github-releases",),
            allowed_purposes=("github-api-token",),
        )
        provider = GitHubReleaseConnectorProvider(
            secrets,
            object(),  # type: ignore[arg-type]
            transport=_UnavailableValidationTransport(),
        )
        connection = Connection(
            id=new_id("connection"),
            connector_type_id=GITHUB_RELEASE_CONNECTOR_TYPE,
            connector_version=GITHUB_RELEASE_CONNECTOR_VERSION,
            owner_type="user",
            owner_id="tester",
            display_name="Issue 751 unavailable validation fixture",
            project_id=project_id,
            secret_references=(reference,),
        )
        operation = OperationContext(
            correlation_id="issue751-validation-unavailable",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )

        with pytest.raises(ContractError) as caught:
            await provider.validate_connection(connection, operation)

        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert caught.value.retryable is True
        assert _TOKEN not in str(caught.value)
        assert _TOKEN not in str(caught.value.details)

    asyncio.run(scenario())
