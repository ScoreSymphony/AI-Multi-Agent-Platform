"""Dependency-aware Registry planning for the browser-first setup lifecycle.

The generic setup service owns progress and coordination. This specialization only enriches its
Registry projection with transitive dependency resolution and exact installed-version reuse; all
actual validation and mutation still delegates to the canonical Registry owner domain.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.distribution import DistributionService
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryDependency, version_key

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


class DependencyAwareBrowserFirstSetupService(BrowserFirstSetupService):
    """Resolve required Registry dependencies into the persisted setup plan before mutation."""

    def __init__(
        self,
        component_setup: OnboardingComponentSetupService,
        onboarding: OnboardingService,
        store: JsonSetupSessionStore,
        *,
        distribution: DistributionService | None = None,
        registry_commands: RegistryCommandHandlers | None = None,
    ) -> None:
        super().__init__(
            component_setup,
            onboarding,
            store,
            distribution=distribution,
            registry_commands=registry_commands,
        )
        # The base service already serializes mutation of durable setup state. This outer lock also
        # keeps the final-outcome replay check atomic with the delegated provisioning operation.
        self._provision_replay_lock = asyncio.Lock()

    async def provision(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Replay the final result of a multi-action operation, not its first partial success."""

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
                        "setup": self.status(context),
                    }
            return await super().provision(context, resource_ref, payload)

    def _plan(
        self,
        session: SetupSessionRecord,
        components: tuple[DiscoveredComponent, ...],
        profile: SetupProfile | None,
    ) -> tuple[_PlanAction, ...]:
        component_session = replace(session, registry_items=())
        component_actions = super()._plan(component_session, components, profile)
        return (*component_actions, *self._registry_dependency_plan(session))

    def _registry_dependency_plan(
        self,
        session: SetupSessionRecord,
    ) -> tuple[_PlanAction, ...]:
        if not session.registry_items:
            return ()
        if self.distribution is None:
            return tuple(self._base_registry_action(session, item) for item in session.registry_items)

        try:
            catalog = self.distribution.search()
        except RuntimeError:
            return tuple(self._base_registry_action(session, item) for item in session.registry_items)

        by_item_id: dict[str, list[RegistryItem]] = {}
        for item in catalog:
            by_item_id.setdefault(item.item_id, []).append(item)
        for items in by_item_id.values():
            items.sort(key=lambda item: version_key(item.version), reverse=True)

        explicit_versions = {item.item_id: item.version for item in session.registry_items}
        resolved_versions = dict(explicit_versions)
        actions_by_ref: dict[str, _PlanAction] = {}
        ordered_refs: list[str] = []
        resolving: list[str] = []

        def visit(selection: RegistrySelection) -> _PlanAction:
            existing = actions_by_ref.get(selection.ref)
            if existing is not None:
                return existing

            try:
                registry_item = self.distribution.get(selection.item_id, selection.version)
            except (LookupError, RuntimeError):
                action = replace(
                    self._base_registry_action(session, selection),
                    kind=ProvisioningActionKind.INSTALL,
                    state=ProvisioningActionState.BLOCKED,
                    blockers=("registry item is not available from the configured Registry",),
                )
                actions_by_ref[selection.ref] = action
                ordered_refs.append(selection.ref)
                return action

            resolving.append(selection.item_id)
            dependency_refs: list[str] = []
            dependency_blockers: list[str] = []
            for dependency in registry_item.dependencies:
                if dependency.optional:
                    continue
                version, blocker = self._resolve_dependency_version(
                    dependency,
                    explicit_versions=explicit_versions,
                    resolved_versions=resolved_versions,
                    catalog=by_item_id,
                )
                if blocker is not None:
                    dependency_blockers.append(
                        f"dependency {dependency.item_id} cannot be resolved: {blocker}"
                    )
                    continue
                assert version is not None
                dependency_ref = f"{dependency.item_id}@{version}"
                dependency_refs.append(dependency_ref)
                if dependency.item_id in resolving:
                    chain = " -> ".join((*resolving, dependency.item_id))
                    dependency_blockers.append(f"registry dependency cycle detected: {chain}")
                    continue
                dependency_action = visit(
                    RegistrySelection(item_id=dependency.item_id, version=version)
                )
                if dependency_action.state in {
                    ProvisioningActionState.BLOCKED,
                    ProvisioningActionState.MANUAL_REQUIRED,
                    ProvisioningActionState.FAILED,
                }:
                    dependency_blockers.append(
                        f"dependency {dependency_ref} is {dependency_action.state.value}"
                    )
            resolving.pop()

            action = self._base_registry_action(session, selection)
            installation = self.distribution.installed(selection.item_id)
            if (
                installation is not None
                and installation.current.version != selection.version
                and action.state is ProvisioningActionState.COMPLETED
            ):
                action = replace(
                    action,
                    kind=ProvisioningActionKind.INSTALL,
                    state=ProvisioningActionState.PENDING,
                )
            if dependency_blockers:
                action = replace(
                    action,
                    kind=(
                        ProvisioningActionKind.MANUAL
                        if action.state is ProvisioningActionState.MANUAL_REQUIRED
                        else ProvisioningActionKind.INSTALL
                    ),
                    state=(
                        action.state
                        if action.state is ProvisioningActionState.MANUAL_REQUIRED
                        else ProvisioningActionState.BLOCKED
                    ),
                    blockers=tuple((*action.blockers, *dependency_blockers)),
                )
            action = replace(action, dependencies=tuple(dependency_refs))
            actions_by_ref[selection.ref] = action
            ordered_refs.append(selection.ref)
            return action

        for selection in session.registry_items:
            visit(selection)
        return tuple(actions_by_ref[ref] for ref in ordered_refs)

    def _resolve_dependency_version(
        self,
        dependency: RegistryDependency,
        *,
        explicit_versions: dict[str, str],
        resolved_versions: dict[str, str],
        catalog: dict[str, list[RegistryItem]],
    ) -> tuple[str | None, str | None]:
        explicit = explicit_versions.get(dependency.item_id)
        if explicit is not None:
            if dependency.version_range.contains(explicit):
                resolved_versions[dependency.item_id] = explicit
                return explicit, None
            return None, f"explicitly selected version {explicit} violates the required range"

        resolved = resolved_versions.get(dependency.item_id)
        if resolved is not None:
            if dependency.version_range.contains(resolved):
                return resolved, None
            return None, f"version {resolved} conflicts with another required dependency range"

        candidates = [
            item
            for item in catalog.get(dependency.item_id, ())
            if dependency.version_range.contains(item.version)
            and not item.deprecated
            and not item.yanked
        ]
        if not candidates:
            return None, "no compatible non-deprecated Registry version is available"
        selected = max(candidates, key=lambda item: version_key(item.version)).version
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

    def _base_registry_action(
        self,
        session: SetupSessionRecord,
        selection: RegistrySelection,
    ) -> _PlanAction:
        return super()._registry_action(session, selection)


__all__ = ["DependencyAwareBrowserFirstSetupService"]
