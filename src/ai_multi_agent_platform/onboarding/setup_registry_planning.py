"""Dependency-aware Registry planning for the browser-first setup lifecycle.

The generic setup service owns progress and coordination. This specialization enriches its Registry
projection with transitive dependency resolution, exact installed-version reuse and reconciliation
against authoritative owner-domain installation state. All validation and mutation still delegates
through the provider-neutral Registry setup port.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext

from .component_setup import OnboardingComponentSetupService
from .components import DiscoveredComponent, SetupProfile
from .service import OnboardingService
from .setup_lifecycle import (
    SETUP_SESSION_RESOURCE_ID,
    BrowserFirstSetupService,
    JsonSetupSessionStore,
    ProvisioningActionKind,
    ProvisioningActionState,
    RegistrySelection,
    SetupSessionRecord,
    _PlanAction,
)
from .setup_registry_contracts import (
    SetupRegistryDependency,
    SetupRegistryItem,
    SetupRegistryPort,
    setup_version_key,
)


@dataclass(slots=True)
class _RegistryPlanState:
    by_item_id: dict[str, list[SetupRegistryItem]]
    explicit_versions: dict[str, str]
    resolved_versions: dict[str, str]
    actions_by_ref: dict[str, _PlanAction] = field(default_factory=dict)
    ordered_refs: list[str] = field(default_factory=list)
    resolving: list[str] = field(default_factory=list)


class DependencyAwareBrowserFirstSetupService(BrowserFirstSetupService):
    """Resolve dependencies and reconcile setup state with Registry owner-domain state."""

    def __init__(
        self,
        component_setup: OnboardingComponentSetupService,
        onboarding: OnboardingService,
        store: JsonSetupSessionStore,
        *,
        registry: SetupRegistryPort | None = None,
    ) -> None:
        super().__init__(
            component_setup,
            onboarding,
            store,
            registry=registry,
        )
        # The base service already serializes mutation of durable setup state. This outer lock also
        # keeps the final-outcome replay check atomic with the delegated provisioning operation.
        self._provision_replay_lock = asyncio.Lock()

    def status(self, context: RequestContext) -> dict[str, JsonValue]:
        """Require all selected automatic mutations to finish before reporting dashboard-ready."""

        return self._enforce_pending_registry_readiness(super().status(context))

    async def status_async(self, context: RequestContext) -> dict[str, JsonValue]:
        """Apply Registry readiness rules to the canonical async first-run projection."""

        return self._enforce_pending_registry_readiness(await super().status_async(context))

    @staticmethod
    def _enforce_pending_registry_readiness(
        status: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        plan = status.get("plan")
        if not isinstance(plan, dict):
            return status
        actions = plan.get("actions")
        if not isinstance(actions, list):
            return status

        pending_action_ids: list[str] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("kind") not in {
                ProvisioningActionKind.INSTALL.value,
                ProvisioningActionKind.ACTIVATE.value,
            }:
                continue
            if action.get("state") != ProvisioningActionState.PENDING.value:
                continue
            action_id = action.get("action_id")
            if isinstance(action_id, str):
                pending_action_ids.append(action_id)
        if not pending_action_ids:
            return status

        readiness = status.get("readiness")
        if isinstance(readiness, dict):
            readiness["ready"] = False
            readiness["dashboard_allowed"] = False
            existing = readiness.get("blocking_actions")
            blocking_action_ids = (
                [value for value in existing if isinstance(value, str)]
                if isinstance(existing, list)
                else []
            )
            readiness["blocking_actions"] = list(
                dict.fromkeys((*blocking_action_ids, *pending_action_ids))
            )

        if status.get("current_step") == "ready":
            status["current_step"] = "validation"
            steps = status.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    if step.get("id") == "validation":
                        step["state"] = "current"
                    elif step.get("id") == "ready":
                        step["state"] = "blocked"
        return status

    async def provision(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Replay final results and preserve dependency ordering for targeted retries."""

        async with self._provision_replay_lock:
            if (
                resource_ref == SETUP_SESSION_RESOURCE_ID
                and not (set(payload) - {"action_ids"})
                and context.idempotency_key is not None
            ):
                session = self._session(context)
                replay = next(
                    (
                        outcome
                        for outcome in reversed(session.outcomes)
                        if outcome.idempotency_key == context.idempotency_key
                    ),
                    None,
                )
                if replay is not None:
                    return {
                        "id": replay.action_id,
                        "type": "provisioning_operation",
                        "replayed": True,
                        "outcome": replay.to_json(),
                        "setup": await self.status_async(context),
                    }
            expanded_payload = self._expand_targeted_dependencies(
                context,
                resource_ref,
                payload,
            )
            return await super().provision(context, resource_ref, expanded_payload)

    def _expand_targeted_dependencies(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Include required dependency actions when a caller retries selected Registry actions."""

        if resource_ref != SETUP_SESSION_RESOURCE_ID or set(payload) - {"action_ids"}:
            return payload
        raw_action_ids = payload.get("action_ids")
        if not isinstance(raw_action_ids, list) or not raw_action_ids:
            return payload
        if any(
            not isinstance(action_id, str) or not action_id.strip() for action_id in raw_action_ids
        ):
            return payload

        requested = set(cast(list[str], raw_action_ids))
        plan = self._plan(
            self._session(context),
            self.component_setup.discovered_components(),
            self.component_setup.active_profile(),
        )
        actions_by_id = {action.action_id: action for action in plan}
        actions_by_ref = {action.component_ref: action for action in plan}
        expanded = set(requested)
        pending = list(requested)
        while pending:
            action_id = pending.pop()
            action = actions_by_id.get(action_id)
            if action is None:
                continue
            for dependency_ref in action.dependencies:
                dependency_action = actions_by_ref.get(dependency_ref)
                if dependency_action is None or dependency_action.action_id in expanded:
                    continue
                expanded.add(dependency_action.action_id)
                pending.append(dependency_action.action_id)

        ordered = [action.action_id for action in plan if action.action_id in expanded]
        ordered.extend(sorted(expanded - set(ordered)))
        return {**payload, "action_ids": cast(JsonValue, ordered)}

    def _plan(
        self,
        session: SetupSessionRecord,
        components: tuple[DiscoveredComponent, ...],
        profile: SetupProfile | None,
    ) -> tuple[_PlanAction, ...]:
        component_actions: tuple[_PlanAction, ...] = ()
        if profile is not None:
            component_session = replace(session, registry_items=())
            component_actions = super()._plan(component_session, components, profile)
        return (*component_actions, *self._registry_dependency_plan(session))

    def _registry_dependency_plan(
        self,
        session: SetupSessionRecord,
    ) -> tuple[_PlanAction, ...]:
        if not session.registry_items:
            return ()
        registry = self.registry
        if registry is None:
            return self._fallback_registry_plan(session)

        try:
            catalog = registry.search()
        except RuntimeError:
            return self._fallback_registry_plan(session)

        state = self._registry_plan_state(session, catalog)
        for selection in session.registry_items:
            self._visit_registry_selection(session, registry, selection, state)
        return tuple(state.actions_by_ref[ref] for ref in state.ordered_refs)

    def _fallback_registry_plan(self, session: SetupSessionRecord) -> tuple[_PlanAction, ...]:
        return tuple(self._base_registry_action(session, item) for item in session.registry_items)

    @staticmethod
    def _registry_plan_state(
        session: SetupSessionRecord,
        catalog: tuple[SetupRegistryItem, ...],
    ) -> _RegistryPlanState:
        by_item_id: dict[str, list[SetupRegistryItem]] = {}
        for item in catalog:
            by_item_id.setdefault(item.item_id, []).append(item)
        for items in by_item_id.values():
            items.sort(key=lambda item: setup_version_key(item.version), reverse=True)
        explicit_versions = {item.item_id: item.version for item in session.registry_items}
        return _RegistryPlanState(
            by_item_id=by_item_id,
            explicit_versions=explicit_versions,
            resolved_versions=dict(explicit_versions),
        )

    def _visit_registry_selection(
        self,
        session: SetupSessionRecord,
        registry: SetupRegistryPort,
        selection: RegistrySelection,
        state: _RegistryPlanState,
    ) -> _PlanAction:
        existing = state.actions_by_ref.get(selection.ref)
        if existing is not None:
            return existing

        try:
            registry_item = registry.get(selection.item_id, selection.version)
        except (LookupError, RuntimeError):
            action = replace(
                self._base_registry_action(session, selection),
                kind=ProvisioningActionKind.INSTALL,
                state=ProvisioningActionState.BLOCKED,
                blockers=("registry item is not available from the configured Registry",),
            )
            return self._record_registry_action(selection, action, state)

        state.resolving.append(selection.item_id)
        try:
            dependency_refs, dependency_blockers = self._registry_dependencies(
                session,
                registry,
                registry_item,
                state,
            )
        finally:
            state.resolving.pop()

        action = self._base_registry_action(session, selection)
        if dependency_blockers:
            action = self._block_registry_action(action, dependency_blockers)
        action = replace(action, dependencies=dependency_refs)
        return self._record_registry_action(selection, action, state)

    def _registry_dependencies(
        self,
        session: SetupSessionRecord,
        registry: SetupRegistryPort,
        registry_item: SetupRegistryItem,
        state: _RegistryPlanState,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        dependency_refs: list[str] = []
        blockers: list[str] = []
        for dependency in registry_item.dependencies:
            if dependency.optional:
                continue
            version, blocker = self._resolve_dependency_version(
                dependency,
                explicit_versions=state.explicit_versions,
                resolved_versions=state.resolved_versions,
                catalog=state.by_item_id,
            )
            if blocker is not None:
                blockers.append(f"dependency {dependency.item_id} cannot be resolved: {blocker}")
                continue

            assert version is not None
            dependency_ref = f"{dependency.item_id}@{version}"
            dependency_refs.append(dependency_ref)
            if dependency.item_id in state.resolving:
                chain = " -> ".join((*state.resolving, dependency.item_id))
                blockers.append(f"registry dependency cycle detected: {chain}")
                continue

            dependency_action = self._visit_registry_selection(
                session,
                registry,
                RegistrySelection(item_id=dependency.item_id, version=version),
                state,
            )
            if dependency_action.state in {
                ProvisioningActionState.BLOCKED,
                ProvisioningActionState.MANUAL_REQUIRED,
                ProvisioningActionState.FAILED,
            }:
                blockers.append(f"dependency {dependency_ref} is {dependency_action.state.value}")
        return tuple(dependency_refs), tuple(blockers)

    @staticmethod
    def _block_registry_action(
        action: _PlanAction,
        blockers: tuple[str, ...],
    ) -> _PlanAction:
        manual = action.state is ProvisioningActionState.MANUAL_REQUIRED
        return replace(
            action,
            kind=ProvisioningActionKind.MANUAL if manual else ProvisioningActionKind.INSTALL,
            state=action.state if manual else ProvisioningActionState.BLOCKED,
            blockers=tuple((*action.blockers, *blockers)),
        )

    @staticmethod
    def _record_registry_action(
        selection: RegistrySelection,
        action: _PlanAction,
        state: _RegistryPlanState,
    ) -> _PlanAction:
        state.actions_by_ref[selection.ref] = action
        state.ordered_refs.append(selection.ref)
        return action

    def _resolve_dependency_version(
        self,
        dependency: SetupRegistryDependency,
        *,
        explicit_versions: dict[str, str],
        resolved_versions: dict[str, str],
        catalog: dict[str, list[SetupRegistryItem]],
    ) -> tuple[str | None, str | None]:
        explicit = explicit_versions.get(dependency.item_id)
        if explicit is not None:
            if dependency.contains(explicit):
                resolved_versions[dependency.item_id] = explicit
                return explicit, None
            return None, f"explicitly selected version {explicit} violates the required range"

        resolved = resolved_versions.get(dependency.item_id)
        if resolved is not None:
            if dependency.contains(resolved):
                return resolved, None
            return None, f"version {resolved} conflicts with another required dependency range"

        candidates = [
            item
            for item in catalog.get(dependency.item_id, ())
            if dependency.contains(item.version) and not item.deprecated and not item.yanked
        ]
        if not candidates:
            return None, "no compatible non-deprecated Registry version is available"
        selected = max(candidates, key=lambda item: setup_version_key(item.version)).version
        resolved_versions[dependency.item_id] = selected
        return selected, None

    def _validate_registry_selections(self, selections: tuple[RegistrySelection, ...]) -> None:
        super()._validate_registry_selections(selections)
        seen_item_ids: set[str] = set()
        for selection in selections:
            if selection.item_id in seen_item_ids:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "setup cannot select multiple versions of the same Registry item",
                    details={"item_id": selection.item_id},
                )
            seen_item_ids.add(selection.item_id)

    def _registry_card(self, item: SetupRegistryItem) -> dict[str, JsonValue]:
        """Project install status for the exact Registry version shown in the product catalog."""

        card = super()._registry_card(item)
        registry = self.registry
        if registry is None:
            return card
        installed_version = registry.installed_version(item.item_id)
        if installed_version is None or installed_version == item.version:
            return card

        if item.deprecated or item.yanked:
            install_status = "blocked"
        elif item.route == "manual" or not registry.mutation_enabled:
            install_status = "manual_required"
        else:
            install_status = "installable"
        card["install_status"] = install_status
        return card

    def _base_registry_action(
        self,
        session: SetupSessionRecord,
        selection: RegistrySelection,
    ) -> _PlanAction:
        """Reconcile persisted outcomes against authoritative Registry installation state."""

        action = super()._registry_action(session, selection)
        registry = self.registry
        if registry is None or not registry.mutation_enabled:
            return action
        if action.state is ProvisioningActionState.BLOCKED:
            return action

        installed_version = registry.installed_version(selection.item_id)
        if installed_version == selection.version:
            return replace(
                action,
                kind=ProvisioningActionKind.REUSE,
                state=ProvisioningActionState.COMPLETED,
                blockers=(),
            )

        if action.state in {
            ProvisioningActionState.MANUAL_REQUIRED,
            ProvisioningActionState.FAILED,
        }:
            return action

        # A completed setup outcome is only a coordination record. If the Registry owner no longer
        # reports the exact selected version as installed, readiness must require provisioning
        # again.
        return replace(
            action,
            kind=ProvisioningActionKind.INSTALL,
            state=ProvisioningActionState.PENDING,
        )


__all__ = ["DependencyAwareBrowserFirstSetupService"]
