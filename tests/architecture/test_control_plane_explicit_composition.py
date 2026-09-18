from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"
GOVERNANCE = ROOT / "src" / "ai_multi_agent_platform" / "governance"

# Inheritance is not a domain ownership/composition mechanism. Ordinary implementation
# inheritance remains out of scope for this boundary. Keep the few pre-existing,
# reviewed implementation compositions explicit so a new ControlPlane MRO stack
# cannot be introduced silently while compatibility layers are retired.
_ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE = {
    ("workspace_task_management_explicit_composition.py", "ControlPlane"): frozenset(
        {
            "RepositoryRunProvenanceMixin",
            "AuthorizationBoundaryHardeningMixin",
            "_RunWorkspaceControlPlane",
        }
    ),
}

# Historical same-domain/implementation layers that still contain direct registration
# internally. None of these is the canonical ownership boundary after #982. Keeping
# this list exact makes a *new* ControlPlane subclass with self.register_* fail CI.
_LEGACY_DIRECT_REGISTRATION_SUBCLASSES = {
    "notifications_composition.py",
    "notifications_authorized_composition.py",
    "notifications_runtime_composition.py",
    "task_management_contract.py",
    "terminal_composition.py",
}

# These canonical later-domain façades have completed their #982 migration. They may
# retain integration methods, but they must not regain private command dispatch or
# direct resource/command registration through another linear subclass layer.
_MIGRATED_LINEAR_DOMAIN_FACADES = {
    "organization_explicit_composition.py",
    "task_project_reassignment.py",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _base_name(base: ast.expr) -> str:
    return ast.unparse(base)


def _method_names(node: ast.ClassDef) -> frozenset[str]:
    return frozenset(
        child.name
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def _direct_registration_calls(node: ast.ClassDef) -> tuple[str, ...]:
    calls: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr not in {"register_resource_service", "register_command"}:
            continue
        if not isinstance(child.func.value, ast.Name) or child.func.value.id != "self":
            continue
        calls.append(f"{child.func.attr}:{child.lineno}")
    return tuple(sorted(calls))


def test_control_plane_domain_facades_do_not_add_unreviewed_multiple_inheritance() -> None:
    """A domain must not regain implicit ownership composition through Python MRO."""

    violations: list[str] = []
    seen_allowlisted: set[tuple[str, str]] = set()
    for path in sorted(CONTROL_PLANE.glob("*.py")):
        for node in _tree(path).body:
            if not (
                isinstance(node, ast.ClassDef)
                and node.name in {"ControlPlane", "ControlPlaneHTTP"}
                and len(node.bases) > 1
            ):
                continue

            key = (path.name, node.name)
            actual = frozenset(_base_name(base) for base in node.bases)
            expected = _ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE.get(key)
            if expected is None:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} adds an unreviewed "
                    f"Control Plane MRO stack: {sorted(actual)!r}"
                )
                continue
            seen_allowlisted.add(key)
            if actual != expected:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} changed reviewed "
                    f"implementation inheritance from {sorted(expected)!r} "
                    f"to {sorted(actual)!r}"
                )

    missing = sorted(set(_ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE) - seen_allowlisted)
    if missing:
        violations.append(
            f"stale Control Plane multiple-inheritance allow-list entries: {missing!r}"
        )
    assert violations == []


def test_new_control_plane_subclasses_cannot_claim_domain_ownership_directly() -> None:
    """New subclass layers must use ControlPlaneModule instead of self.register_* ownership."""

    violations: list[str] = []
    seen_legacy: set[str] = set()
    for path in sorted(CONTROL_PLANE.glob("*.py")):
        try:
            facade = _class(path, "ControlPlane")
        except AssertionError:
            continue
        calls = _direct_registration_calls(facade)
        if not calls:
            continue
        if path.name in _LEGACY_DIRECT_REGISTRATION_SUBCLASSES:
            seen_legacy.add(path.name)
            continue
        violations.append(
            f"{path.relative_to(ROOT)} directly claims resources/commands from a "
            f"ControlPlane subclass: {calls!r}"
        )

    stale = sorted(_LEGACY_DIRECT_REGISTRATION_SUBCLASSES - seen_legacy)
    if stale:
        violations.append(f"stale direct-registration compatibility entries: {stale!r}")
    assert violations == []


def test_migrated_linear_domain_facades_do_not_regain_dispatch_ownership() -> None:
    forbidden = {"execute_command", "register_command", "register_resource_service"}
    for filename in sorted(_MIGRATED_LINEAR_DOMAIN_FACADES):
        facade = _class(CONTROL_PLANE / filename, "ControlPlane")
        regained = sorted(_method_names(facade).intersection(forbidden))
        assert regained == [], f"{filename} regained domain dispatch/registration: {regained!r}"


def test_migrated_compatibility_facades_do_not_own_domain_behavior() -> None:
    """Compatibility classes may install modules but may not reimplement domain behavior."""

    allowed = {
        "portability_api.py": {"__init__", "portability_workflow"},
        "plugin_api.py": {"__init__", "plugin_registry", "plugin_catalog", "attach_plugin_runtime"},
        "organization_audit_api.py": {"__init__", "organization_audit"},
        "approval_decision_composition.py": {"__init__"},
    }
    for filename, allowed_methods in allowed.items():
        facade = _class(CONTROL_PLANE / filename, "ControlPlane")
        methods = {
            node.name
            for node in facade.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert methods <= allowed_methods, (
            f"{filename} compatibility facade regained domain behavior: "
            f"{sorted(methods - allowed_methods)!r}"
        )


def test_hardened_automation_is_behavior_free_compatibility_import() -> None:
    path = CONTROL_PLANE / "hardened_automation_api.py"
    tree = _tree(path)
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ControlPlane" for node in tree.body
    )
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "automation_explicit_composition"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_organization_runtime_is_behavior_free_compatibility_import() -> None:
    path = CONTROL_PLANE / "organization_runtime_composition.py"
    tree = _tree(path)
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "organization_explicit_composition"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_organization_audit_uses_explicit_organization_composition() -> None:
    path = CONTROL_PLANE / "organization_audit_api.py"
    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(node.module == "organization_runtime_composition" for node in imports)
    assert any(
        node.module == "organization_explicit_composition"
        and any(alias.asname == "_OrganizationControlPlane" for alias in node.names)
        for node in imports
    )


def test_release_status_special_route_is_explicitly_owned() -> None:
    path = CONTROL_PLANE / "release_api.py"
    source = path.read_text(encoding="utf-8")
    assert 'RELEASE_STATUS_MODULE = "release-status"' in source
    assert "ControlPlaneRoute(" in source
    assert "ControlPlaneModule(" in source

    facade = _class(path, "ControlPlaneHTTP")
    handle = next(
        node
        for node in facade.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle"
    )
    assert "RELEASE_STATUS_PATH" not in ast.unparse(handle)


def test_canonical_single_node_portability_composition_has_one_control_plane_base() -> None:
    path = CONTROL_PLANE / "approval_portability_composition.py"
    facade = _class(path, "ControlPlane")
    assert len(facade.bases) == 1

    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module == "portability_api"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_canonical_authorization_uses_explicit_automation_composition() -> None:
    path = CONTROL_PLANE / "authenticated_authorization.py"
    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]

    assert not any(
        node.module == "hardened_automation_api"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )
    assert any(
        node.module == "automation_explicit_composition"
        and any(alias.asname == "_CurrentControlPlane" for alias in node.names)
        for node in imports
    )


def test_plugin_terminal_composition_has_one_control_plane_base() -> None:
    path = CONTROL_PLANE / "plugin_terminal_composition.py"
    facade = _class(path, "ControlPlane")
    assert len(facade.bases) == 1

    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module in {"plugin_api", "terminal_composition"}
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )
    assert any(
        node.module == "terminal_explicit_composition"
        and any(alias.asname == "_TerminalControlPlane" for alias in node.names)
        for node in imports
    )


def test_current_conversation_composition_has_one_control_plane_base() -> None:
    path = CONTROL_PLANE / "conversation_current_composition.py"
    facade = _class(path, "ControlPlane")
    assert len(facade.bases) == 1
    assert _base_name(facade.bases[0]) == "_NotificationControlPlane"

    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module in {"conversation_composition", "notifications_plugin_composition"}
        and any(
            alias.name == "ControlPlane" or alias.asname == "_ConversationControlPlane"
            for alias in node.names
        )
        for node in imports
    )
    assert any(
        node.module == "notifications_explicit_composition"
        and any(alias.asname == "_NotificationControlPlane" for alias in node.names)
        for node in imports
    )


def test_migrated_domains_declare_explicit_module_owners() -> None:
    portability = (CONTROL_PLANE / "portability_module.py").read_text(encoding="utf-8")
    plugins = (CONTROL_PLANE / "plugin_module.py").read_text(encoding="utf-8")
    organization = (CONTROL_PLANE / "organization_explicit_composition.py").read_text(
        encoding="utf-8"
    )
    organization_audit = (CONTROL_PLANE / "organization_audit_api.py").read_text(encoding="utf-8")
    approval_decisions = (CONTROL_PLANE / "approval_decision_module.py").read_text(encoding="utf-8")
    automation = (CONTROL_PLANE / "automation_explicit_composition.py").read_text(encoding="utf-8")
    conversations = (CONTROL_PLANE / "conversation_module.py").read_text(encoding="utf-8")
    notifications = (CONTROL_PLANE / "notifications_explicit_composition.py").read_text(
        encoding="utf-8"
    )
    terminal = (CONTROL_PLANE / "terminal_module.py").read_text(encoding="utf-8")
    task_project = (CONTROL_PLANE / "task_project_reassignment.py").read_text(encoding="utf-8")
    release = (CONTROL_PLANE / "release_api.py").read_text(encoding="utf-8")
    product = (CONTROL_PLANE / "approval_portability_composition.py").read_text(encoding="utf-8")
    governance = (GOVERNANCE / "control_plane_module.py").read_text(encoding="utf-8")

    assert 'PORTABILITY_MODULE = "portability"' in portability
    assert 'PLUGIN_MODULE = "plugins"' in plugins
    assert 'ORGANIZATION_MODULE = "organizations"' in organization
    assert 'ACCOUNTING_MODULE = "accounting"' in organization
    assert 'ORGANIZATION_AUDIT_MODULE = "organization-audit"' in organization_audit
    assert 'APPROVAL_DECISION_MODULE = "approval-decisions"' in approval_decisions
    assert 'AUTOMATION_MODULE = "automation"' in automation
    assert 'CONVERSATION_MODULE = "conversations"' in conversations
    assert 'NOTIFICATION_MODULE = "notifications"' in notifications
    assert 'TERMINAL_MODULE = "terminal"' in terminal
    assert 'TASK_PROJECT_REASSIGNMENT_MODULE = "task-project-reassignment"' in task_project
    assert 'RELEASE_STATUS_MODULE = "release-status"' in release
    assert 'GOAL_MODULE = "goals"' in product
    assert 'DECISION_RECORD_MODULE = "decision-records"' in product
    assert 'GOVERNANCE_MODULE = "governance"' in governance

    for source in (
        portability,
        plugins,
        organization,
        organization_audit,
        approval_decisions,
        automation,
        conversations,
        notifications,
        terminal,
        task_project,
        release,
        product,
        governance,
    ):
        assert "ControlPlaneModule(" in source
