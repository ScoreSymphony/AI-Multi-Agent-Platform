from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECURITY_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "security"
FACADE = SECURITY_ROOT / "authentication.py"
ACCOUNTS = SECURITY_ROOT / "authentication_accounts.py"
SESSIONS = SECURITY_ROOT / "authentication_sessions.py"
CREDENTIALS = SECURITY_ROOT / "authentication_credentials.py"
EXTERNAL = SECURITY_ROOT / "authentication_external.py"
FOCUSED_MODULES = (
    ACCOUNTS,
    SECURITY_ROOT / "authentication_audit.py",
    CREDENTIALS,
    EXTERNAL,
    SECURITY_ROOT / "authentication_models.py",
    SECURITY_ROOT / "authentication_passwords.py",
    SECURITY_ROOT / "authentication_protection.py",
    SECURITY_ROOT / "authentication_serialization.py",
    SESSIONS,
    SECURITY_ROOT / "authentication_store.py",
    SECURITY_ROOT / "authentication_tokens.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} does not define {name}")


def _assert_delegate(
    method: ast.FunctionDef | ast.AsyncFunctionDef, component: str, call: str
) -> None:
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(item.func, ast.Attribute)
        and item.func.attr == call
        and isinstance(item.func.value, ast.Attribute)
        and isinstance(item.func.value.value, ast.Name)
        and item.func.value.value.id == "self"
        and item.func.value.attr == component
        for item in calls
    ), f"{method.name} must delegate to self.{component}.{call}"


def _assert_no_facade_dependency(path: Path) -> None:
    violations: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "authentication",
            "ai_multi_agent_platform.security.authentication",
        }:
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import) and any(
            alias.name == "ai_multi_agent_platform.security.authentication" for alias in node.names
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, (
        "focused authentication components must not depend back on the stable façade:\n"
        + "\n".join(violations)
    )


def test_account_entrypoints_delegate_to_account_service() -> None:
    facade = _class(FACADE, "LocalAuthenticationService")
    for method_name in (
        "bootstrap_first_admin",
        "create_local_user",
        "authenticate_password",
        "change_password",
        "reset_local_password",
        "set_account_enabled",
        "set_account_locked",
    ):
        _assert_delegate(_method(facade, method_name), "_accounts", method_name)


def test_session_entrypoints_delegate_to_session_service() -> None:
    facade = _class(FACADE, "LocalAuthenticationService")
    for method_name in (
        "create_browser_session",
        "authenticate_session",
        "logout",
        "list_sessions",
        "revoke_session",
    ):
        _assert_delegate(_method(facade, method_name), "_sessions", method_name)


def test_credential_entrypoints_delegate_to_credential_service() -> None:
    facade = _class(FACADE, "LocalAuthenticationService")
    for method_name in (
        "create_credential",
        "authenticate_bearer",
        "list_credentials",
        "revoke_credential",
    ):
        _assert_delegate(_method(facade, method_name), "_credentials", method_name)
    _assert_delegate(
        _method(facade, "authenticate_worker_request"),
        "_credentials",
        "authenticate_worker_actor",
    )


def test_external_identity_entrypoints_delegate_to_external_service() -> None:
    facade = _class(FACADE, "LocalAuthenticationService")
    _assert_delegate(
        _method(facade, "link_external_identity"),
        "_external_identities",
        "link_external_identity",
    )
    _assert_delegate(
        _method(facade, "authenticate_external"),
        "_external_identities",
        "authenticate_external",
    )


def test_focused_authentication_components_do_not_depend_back_on_facade() -> None:
    for path in FOCUSED_MODULES:
        assert path.exists(), f"missing focused authentication component: {path.relative_to(ROOT)}"
        _assert_no_facade_dependency(path)


def test_security_sensitive_mechanics_are_not_reimplemented_in_facade() -> None:
    tree = _tree(FACADE)
    imported_modules = {
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    }
    assert "hmac" not in imported_modules
    assert "secrets" not in imported_modules


def test_focused_services_own_primary_authentication_responsibilities() -> None:
    _class(ACCOUNTS, "AuthenticationAccountService")
    _class(SESSIONS, "AuthenticationSessionService")
    _class(CREDENTIALS, "AuthenticationCredentialService")
    _class(EXTERNAL, "AuthenticationExternalIdentityService")
