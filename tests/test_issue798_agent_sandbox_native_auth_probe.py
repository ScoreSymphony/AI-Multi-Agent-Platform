from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = Path("scripts/benchmarks/issue798_agent_sandbox_native_auth_probe.py")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("issue798_native_auth_probe", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(*, authorized: bool | None, transport_error: str | None = None) -> dict[str, Any]:
    return {
        "status": 200 if authorized is True else (403 if authorized is False else None),
        "authorized": authorized,
        "transport_error": transport_error,
    }


def test_surface_urls_quote_sandbox_name_and_file_path() -> None:
    module = _module()
    urls = module._surface_urls(
        "http://127.0.0.1:10000/api/v1/",
        "sandbox a/with slash",
        "/workspace/a b",
    )

    assert urls["native_sandbox_get"] == (
        "http://127.0.0.1:10000/api/v1/sandbox/sandbox%20a%2Fwith%20slash"
    )
    assert urls["native_logs_get"].endswith(
        "/logs/sandbox/sandbox%20a%2Fwith%20slash?tailLines=1"
    )
    assert urls["native_files_list"].endswith(
        "/sandbox/files/sandbox%20a%2Fwith%20slash?path=%2Fworkspace%2Fa+b"
    )


def test_matrix_passes_only_when_same_tenant_works_and_cross_tenant_is_blocked() -> None:
    module = _module()

    def requester(url: str, token: str) -> dict[str, Any]:
        owns_target = (token == "token-a" and "sandbox-a" in url) or (
            token == "token-b" and "sandbox-b" in url
        )
        return _result(authorized=owns_target)

    matrix = module._probe_matrix(
        base_url="http://provider/api/v1",
        sandbox_a="sandbox-a",
        sandbox_b="sandbox-b",
        token_a="token-a",
        token_b="token-b",
        file_list_path="/",
        requester=requester,
    )

    assert matrix["all_transport_complete"] is True
    assert matrix["all_same_tenant_access"] is True
    assert matrix["all_cross_tenant_blocked"] is True
    assert matrix["all_ownership_shapes_passed"] is True
    assert all(
        surface["ownership_shape_passed"] is True
        for surface in matrix["surfaces"].values()
    )


def test_matrix_rejects_allow_everything_as_cross_tenant_exposure() -> None:
    module = _module()

    def requester(url: str, token: str) -> dict[str, Any]:
        del url, token
        return _result(authorized=True)

    matrix = module._probe_matrix(
        base_url="http://provider/api/v1",
        sandbox_a="sandbox-a",
        sandbox_b="sandbox-b",
        token_a="token-a",
        token_b="token-b",
        file_list_path="/",
        requester=requester,
    )

    assert matrix["all_same_tenant_access"] is True
    assert matrix["all_cross_tenant_blocked"] is False
    assert matrix["all_ownership_shapes_passed"] is False


def test_matrix_rejects_deny_everything_as_nonfunctional_authorization() -> None:
    module = _module()

    def requester(url: str, token: str) -> dict[str, Any]:
        del url, token
        return _result(authorized=False)

    matrix = module._probe_matrix(
        base_url="http://provider/api/v1",
        sandbox_a="sandbox-a",
        sandbox_b="sandbox-b",
        token_a="token-a",
        token_b="token-b",
        file_list_path="/",
        requester=requester,
    )

    assert matrix["all_same_tenant_access"] is False
    assert matrix["all_cross_tenant_blocked"] is True
    assert matrix["all_ownership_shapes_passed"] is False


def test_transport_failure_never_counts_as_ownership_pass() -> None:
    module = _module()

    def requester(url: str, token: str) -> dict[str, Any]:
        del url, token
        return _result(authorized=None, transport_error="ConnectionRefusedError")

    matrix = module._probe_matrix(
        base_url="http://provider/api/v1",
        sandbox_a="sandbox-a",
        sandbox_b="sandbox-b",
        token_a="token-a",
        token_b="token-b",
        file_list_path="/",
        requester=requester,
    )

    assert matrix["all_transport_complete"] is False
    assert matrix["all_ownership_shapes_passed"] is False
