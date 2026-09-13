from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"
GOVERNANCE = ROOT / "src" / "ai_multi_agent_platform" / "governance"

# #982 removes inheritance as a *domain ownership/composition* mechanism. It does
# not outlaw every use of implementation inheritance (the issue explicitly keeps
# ordinary implementation inheritance out of scope). Keep the few pre-existing,
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


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _base_name(base: ast.expr) -> str:
    return ast.unparse(base)


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
            "stale Control Plane multiple-inheritance allow-list entries: "
            f"{missing!r}"
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
    organization_audit = (CONTROL_PLANE / "organization_audit_api.py").read_text(
        encoding="utf-8"
    )
    approval_decisions = (CONTROL_PLANE / "approval_decision_module.py").read_text(
        encoding="utf-8"
    )
    automation = (CONTROL_PLANE / "automation_explicit_composition.py").read_text(
        encoding="utf-8"
    )
    conversations = (CONTROL_PLANE / "conversation_module.py").read_text(encoding="utf-8")
    notifications = (CONTROL_PLANE / "notifications_explicit_composition.py").read_text(
        encoding="utf-8"
    )
    terminal = (CONTROL_PLANE / "terminal_module.py").read_text(encoding="utf-8")
    product = (CONTROL_PLANE / "approval_portability_composition.py").read_text(
        encoding="utf-8"
    )
    governance = (GOVERNANCE / "control_plane_module.py").read_text(encoding="utf-8")

    assert 'PORTABILITY_MODULE = "portability"' in portability
    assert 'PLUGIN_MODULE = "plugins"' in plugins
    assert 'ORGANIZATION_AUDIT_MODULE = "organization-audit"' in organization_audit
    assert 'APPROVAL_DECISION_MODULE = "approval-decisions"' in approval_decisions
    assert 'AUTOMATION_MODULE = "automation"' in automation
    assert 'CONVERSATION_MODULE = "conversations"' in conversations
    assert 'NOTIFICATION_MODULE = "notifications"' in notifications
    assert 'TERMINAL_MODULE = "terminal"' in terminal
    assert 'GOAL_MODULE = "goals"' in product
    assert 'DECISION_RECORD_MODULE = "decision-records"' in product
    assert 'GOVERNANCE_MODULE = "governance"' in governance

    for source in (
        portability,
        plugins,
        organization_audit,
        approval_decisions,
        automation,
        conversations,
        notifications,
        terminal,
        product,
        governance,
    ):
        assert "ControlPlaneModule(" in source
