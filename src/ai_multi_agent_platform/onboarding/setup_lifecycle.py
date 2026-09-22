"""Persistent browser-first setup planning and provisioning coordination.

This module deliberately coordinates existing owner domains instead of becoming another installer.
Component discovery/profile state remains owned by :mod:`onboarding.component_setup`, model setup
remains owned by :class:`OnboardingService`, and Registry mutation is delegated through the
provider-neutral setup Registry port. Durable state here contains only navigation/progress,
Registry references and redacted operation outcomes; credentials and secret values are never
accepted.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext

from .component_setup import OnboardingComponentSetupService
from .components import (
    CompatibilityState,
    ComponentAvailability,
    ComponentCategory,
    DiscoveredComponent,
    SetupProfile,
)
from .service import OnboardingService
from .setup_catalog import project_registry_card, registry_category
from .setup_registry_contracts import SetupRegistryItem, SetupRegistryPort

SETUP_SESSION_RESOURCE_ID = "initial-setup"
SETUP_SESSION_SCHEMA_VERSION = "1"
ONBOARDING_UPDATE_SETUP_SESSION_COMMAND = "onboarding.update-setup-session"
ONBOARDING_PROVISION_SETUP_COMMAND = "onboarding.provision-setup"
ONBOARDING_VALIDATE_SETUP_COMMAND = "onboarding.validate-setup"


class SetupStep(StrEnum):
    IDENTITY = "identity"
    ENVIRONMENT = "environment"
    COMPONENTS = "components"
    CONFIGURATION = "configuration"
    VALIDATION = "validation"
    READY = "ready"


class SetupStepState(StrEnum):
    PENDING = "pending"
    CURRENT = "current"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class ProvisioningActionKind(StrEnum):
    REUSE = "reuse"
    INSTALL = "install"
    CONFIGURE = "configure"
    ACTIVATE = "activate"
    VALIDATE = "validate"
    MANUAL = "manual"


class ProvisioningActionState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    MANUAL_REQUIRED = "manual_required"


@dataclass(frozen=True, slots=True)
class RegistrySelection:
    item_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.item_id.strip() or not self.version.strip():
            raise ValueError("registry setup selection requires non-blank item_id and version")

    @property
    def ref(self) -> str:
        return f"{self.item_id}@{self.version}"

    def to_json(self) -> dict[str, JsonValue]:
        return {"item_id": self.item_id, "version": self.version}


@dataclass(frozen=True, slots=True)
class ProvisioningOutcome:
    action_id: str
    state: ProvisioningActionState
    idempotency_key: str
    updated_at: str
    error_code: str | None = None
    error_message: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "action_id": self.action_id,
            "state": self.state.value,
            "idempotency_key": self.idempotency_key,
            "updated_at": self.updated_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class SetupSessionRecord:
    principal_ref: str
    current_step: SetupStep = SetupStep.ENVIRONMENT
    registry_items: tuple[RegistrySelection, ...] = ()
    outcomes: tuple[ProvisioningOutcome, ...] = ()
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def outcome_for(self, action_id: str) -> ProvisioningOutcome | None:
        return next((item for item in self.outcomes if item.action_id == action_id), None)

    def replay_for(self, idempotency_key: str) -> ProvisioningOutcome | None:
        return next(
            (item for item in self.outcomes if item.idempotency_key == idempotency_key),
            None,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "principal_ref": self.principal_ref,
            "current_step": self.current_step.value,
            "registry_items": [item.to_json() for item in self.registry_items],
            "outcomes": [item.to_json() for item in self.outcomes],
            "updated_at": self.updated_at,
        }


class JsonSetupSessionStore:
    """Atomic durable setup-session persistence containing no credential values."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, SetupSessionRecord]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("setup session document must be an object")
        if raw.get("schema_version") != SETUP_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported setup session schema version")
        sessions = raw.get("sessions")
        if not isinstance(sessions, list):
            raise ValueError("setup session document must contain a sessions list")
        records = tuple(_session_from_json(item) for item in sessions)
        return {record.principal_ref: record for record in records}

    def save(self, sessions: dict[str, SetupSessionRecord]) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": SETUP_SESSION_SCHEMA_VERSION,
            "sessions": [sessions[key].to_json() for key in sorted(sessions)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


@dataclass(frozen=True, slots=True)
class _PlanAction:
    action_id: str
    kind: ProvisioningActionKind
    state: ProvisioningActionState
    component_ref: str
    display_name: str
    category: str
    dependencies: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    owner: str = "platform"
    version: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "component_ref": self.component_ref,
            "display_name": self.display_name,
            "category": self.category,
            "dependencies": list(self.dependencies),
            "blockers": list(self.blockers),
            "owner": self.owner,
            "version": self.version,
        }


class BrowserFirstSetupService:
    """Coordinate resumable first-run planning without owning component lifecycles."""

    def __init__(
        self,
        component_setup: OnboardingComponentSetupService,
        onboarding: OnboardingService,
        store: JsonSetupSessionStore,
        *,
        registry: SetupRegistryPort | None = None,
    ) -> None:
        self.component_setup = component_setup
        self.onboarding = onboarding
        self.store = store
        self.registry = registry
        self._sessions = store.load()
        self._lock = asyncio.Lock()

    def status(self, context: RequestContext) -> dict[str, JsonValue]:
        """Return setup state for legacy synchronous onboarding compositions."""

        return self._status(context, self.onboarding.status(context))

    async def status_async(self, context: RequestContext) -> dict[str, JsonValue]:
        """Return setup state from the canonical asynchronous onboarding projection."""

        return self._status(context, await self.onboarding.status_async(context))

    def _status(
        self,
        context: RequestContext,
        onboarding_status: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        session = self._session(context)
        components = self.component_setup.discovered_components()
        profile = self.component_setup.active_profile()
        plan = self._plan(session, components, profile)
        steps = self._steps(session, profile, plan, onboarding_status)
        ready = (
            onboarding_status.get("state") == "ready_for_task"
            and profile is not None
            and not _plan_blocked(plan)
            and all(action.state is not ProvisioningActionState.FAILED for action in plan)
        )
        return {
            "id": SETUP_SESSION_RESOURCE_ID,
            "type": "setup_session",
            "current_step": SetupStep.READY.value if ready else session.current_step.value,
            "steps": [{"id": step.value, "state": state.value} for step, state in steps],
            "catalog": self._catalog(components),
            "registry_items": [item.to_json() for item in session.registry_items],
            "active_profile_id": profile.profile_id if profile else None,
            "plan": {
                "actions": [action.to_json() for action in plan],
                "blocking": _plan_blocked(plan),
                "mutation_required": any(
                    action.kind in {ProvisioningActionKind.INSTALL, ProvisioningActionKind.ACTIVATE}
                    and action.state is ProvisioningActionState.PENDING
                    for action in plan
                ),
            },
            "readiness": {
                "ready": ready,
                "dashboard_allowed": ready,
                "canonical_onboarding_state": onboarding_status.get("state"),
                "blocking_actions": [
                    action.action_id
                    for action in plan
                    if action.state
                    in {
                        ProvisioningActionState.BLOCKED,
                        ProvisioningActionState.MANUAL_REQUIRED,
                        ProvisioningActionState.FAILED,
                    }
                ],
            },
            "updated_at": session.updated_at,
        }

    async def update_session(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self._require_resource(resource_ref)
        _reject_unknown_fields(payload, {"current_step", "registry_items"})
        async with self._lock:
            current = self._session(context)
            step = current.current_step
            if "current_step" in payload:
                raw_step = payload["current_step"]
                if not isinstance(raw_step, str):
                    raise ContractError(ErrorCode.INVALID_REQUEST, "current_step must be a string")
                try:
                    step = SetupStep(raw_step)
                except ValueError as exc:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST, "unsupported setup step"
                    ) from exc
                if step in {SetupStep.IDENTITY, SetupStep.READY}:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        "identity and ready setup steps are server-derived",
                    )
            registry_items = current.registry_items
            if "registry_items" in payload:
                registry_items = _registry_selections(payload["registry_items"])
                self._validate_registry_selections(registry_items)
            updated = SetupSessionRecord(
                principal_ref=current.principal_ref,
                current_step=step,
                registry_items=registry_items,
                outcomes=current.outcomes,
            )
            self._sessions[current.principal_ref] = updated
            self.store.save(self._sessions)
        return await self.status_async(context)

    async def provision(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self._require_resource(resource_ref)
        _reject_unknown_fields(payload, {"action_ids"})
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "setup provisioning requires an idempotency key",
            )
        requested = _action_ids(payload.get("action_ids"))
        async with self._lock:
            session = self._session(context)
            replay = session.replay_for(context.idempotency_key)
            if replay is not None:
                return {
                    "id": replay.action_id,
                    "type": "provisioning_operation",
                    "replayed": True,
                    "outcome": replay.to_json(),
                    "setup": await self.status_async(context),
                }
            components = self.component_setup.discovered_components()
            profile = self.component_setup.active_profile()
            plan = self._plan(session, components, profile)
            candidates = tuple(
                action
                for action in plan
                if action.kind in {ProvisioningActionKind.INSTALL, ProvisioningActionKind.ACTIVATE}
                and action.state
                in {ProvisioningActionState.PENDING, ProvisioningActionState.FAILED}
                and (not requested or action.action_id in requested)
            )
            if requested:
                known = {action.action_id for action in plan}
                unknown = sorted(requested - known)
                if unknown:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        "setup provisioning requested unknown action IDs",
                        details={"action_ids": cast(list[JsonValue], unknown)},
                    )
            if not candidates:
                return {
                    "id": "setup-noop",
                    "type": "provisioning_operation",
                    "replayed": False,
                    "outcome": None,
                    "setup": await self.status_async(context),
                }

            outcomes = list(session.outcomes)
            latest: ProvisioningOutcome | None = None
            for action in candidates:
                latest = await self._execute_registry_action(context, action)
                outcomes = [item for item in outcomes if item.action_id != action.action_id]
                outcomes.append(latest)
                if latest.state is ProvisioningActionState.FAILED:
                    break
            updated = SetupSessionRecord(
                principal_ref=session.principal_ref,
                current_step=(
                    SetupStep.VALIDATION
                    if latest is not None and latest.state is ProvisioningActionState.COMPLETED
                    else session.current_step
                ),
                registry_items=session.registry_items,
                outcomes=tuple(outcomes),
            )
            self._sessions[session.principal_ref] = updated
            self.store.save(self._sessions)
            return {
                "id": latest.action_id if latest else "setup-noop",
                "type": "provisioning_operation",
                "replayed": False,
                "outcome": latest.to_json() if latest else None,
                "setup": await self.status_async(context),
            }

    async def validate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self._require_resource(resource_ref)
        if payload:
            raise ContractError(ErrorCode.INVALID_REQUEST, "setup validation accepts no fields")
        async with self._lock:
            session = self._session(context)
            setup = await self.status_async(context)
            readiness = setup["readiness"]
            ready = isinstance(readiness, dict) and readiness.get("ready") is True
            updated = SetupSessionRecord(
                principal_ref=session.principal_ref,
                current_step=SetupStep.READY if ready else SetupStep.VALIDATION,
                registry_items=session.registry_items,
                outcomes=session.outcomes,
            )
            self._sessions[session.principal_ref] = updated
            self.store.save(self._sessions)
            return await self.status_async(context)

    def _session(self, context: RequestContext) -> SetupSessionRecord:
        principal_ref = context.actor.principal_ref
        existing = self._sessions.get(principal_ref)
        if existing is not None:
            return existing
        return SetupSessionRecord(principal_ref=principal_ref)

    def _plan(
        self,
        session: SetupSessionRecord,
        components: tuple[DiscoveredComponent, ...],
        profile: SetupProfile | None,
    ) -> tuple[_PlanAction, ...]:
        actions: list[_PlanAction] = []
        by_key = {component.key: component for component in components}
        environment = self.component_setup.environment()
        if profile is not None:
            for category, component_id in sorted(
                profile.defaults.items(), key=lambda item: item[0].value
            ):
                component = by_key.get((category, component_id))
                if component is None:
                    actions.append(
                        _PlanAction(
                            action_id=f"component:{category.value}:{component_id}",
                            kind=ProvisioningActionKind.MANUAL,
                            state=ProvisioningActionState.BLOCKED,
                            component_ref=component_id,
                            display_name=component_id,
                            category=category.value,
                            blockers=("selected component is not currently discovered",),
                        )
                    )
                    continue
                compatibility = self.component_setup.resolver.resolve(component, environment)
                actions.append(
                    self._component_action(component, compatibility.state, compatibility.reasons)
                )

        for selection in session.registry_items:
            actions.append(self._registry_action(session, selection))
        return tuple(actions)

    def _component_action(
        self,
        component: DiscoveredComponent,
        compatibility: CompatibilityState,
        reasons: tuple[str, ...],
    ) -> _PlanAction:
        action_id = f"component:{component.category.value}:{component.component_id}"
        if component.availability is ComponentAvailability.AVAILABLE and compatibility in {
            CompatibilityState.COMPATIBLE,
            CompatibilityState.COMPATIBLE_WITH_CONSTRAINTS,
            CompatibilityState.EXPERIMENTAL,
        }:
            return _PlanAction(
                action_id=action_id,
                kind=ProvisioningActionKind.REUSE,
                state=ProvisioningActionState.COMPLETED,
                component_ref=component.component_id,
                display_name=component.display_name,
                category=component.category.value,
                dependencies=tuple(
                    req.capability for req in component.requirements if req.required
                ),
                owner=_component_owner(component),
                version=component.version,
            )
        if component.availability is ComponentAvailability.INSTALLATION_REQUIRED:
            parsed = _registry_source(component.source_ref)
            if parsed is not None and self.registry is not None and self.registry.mutation_enabled:
                item_id, version = parsed
                return _PlanAction(
                    action_id=f"registry:{item_id}@{version}",
                    kind=ProvisioningActionKind.INSTALL,
                    state=ProvisioningActionState.PENDING,
                    component_ref=f"{item_id}@{version}",
                    display_name=component.display_name,
                    category=component.category.value,
                    blockers=reasons,
                    owner="registry",
                    version=version,
                )
            return _PlanAction(
                action_id=action_id,
                kind=ProvisioningActionKind.MANUAL,
                state=ProvisioningActionState.MANUAL_REQUIRED,
                component_ref=component.component_id,
                display_name=component.display_name,
                category=component.category.value,
                blockers=reasons or ("no canonical automatic install authority is configured",),
                owner=_component_owner(component),
                version=component.version,
            )
        return _PlanAction(
            action_id=action_id,
            kind=ProvisioningActionKind.VALIDATE,
            state=ProvisioningActionState.BLOCKED,
            component_ref=component.component_id,
            display_name=component.display_name,
            category=component.category.value,
            blockers=reasons or (f"component is {compatibility.value}",),
            owner=_component_owner(component),
            version=component.version,
        )

    def _registry_action(
        self,
        session: SetupSessionRecord,
        selection: RegistrySelection,
    ) -> _PlanAction:
        action_id = f"registry:{selection.ref}"
        outcome = session.outcome_for(action_id)
        registry = self.registry
        if outcome is not None:
            state = outcome.state
        elif registry is None or not registry.mutation_enabled:
            state = ProvisioningActionState.MANUAL_REQUIRED
        elif registry.installed_version(selection.item_id) is not None:
            state = ProvisioningActionState.COMPLETED
        else:
            state = ProvisioningActionState.PENDING
        blockers: tuple[str, ...] = ()
        display_name = selection.item_id
        category = "tools_mcp"
        if registry is not None:
            try:
                item = registry.get(selection.item_id, selection.version)
            except (LookupError, RuntimeError):
                state = ProvisioningActionState.BLOCKED
                blockers = ("registry item is not available from the configured Registry",)
            else:
                display_name = item.name
                category = registry_category(item.categories)
                if item.route == "manual":
                    state = ProvisioningActionState.MANUAL_REQUIRED
                    blockers = ("registry item declares a manual distribution route",)
                if item.deprecated or item.yanked:
                    state = ProvisioningActionState.BLOCKED
                    blockers = ("registry item is deprecated or yanked",)
        return _PlanAction(
            action_id=action_id,
            kind=(
                ProvisioningActionKind.INSTALL
                if state
                not in {ProvisioningActionState.MANUAL_REQUIRED, ProvisioningActionState.BLOCKED}
                else ProvisioningActionKind.MANUAL
            ),
            state=state,
            component_ref=selection.ref,
            display_name=display_name,
            category=category,
            blockers=blockers,
            owner="registry",
            version=selection.version,
        )

    async def _execute_registry_action(
        self,
        context: RequestContext,
        action: _PlanAction,
    ) -> ProvisioningOutcome:
        now = datetime.now(UTC).isoformat()
        key = context.idempotency_key
        assert key is not None
        registry = self.registry
        if registry is None or not registry.mutation_enabled:
            return ProvisioningOutcome(
                action_id=action.action_id,
                state=ProvisioningActionState.FAILED,
                idempotency_key=key,
                updated_at=now,
                error_code=ErrorCode.CONFLICT.value,
                error_message="Registry activation authority is not configured",
            )
        item_id, version = _action_registry_ref(action)
        try:
            await registry.preview(context, item_id, version)
            await registry.activate(context, item_id, version)
        except ContractError as exc:
            return ProvisioningOutcome(
                action_id=action.action_id,
                state=ProvisioningActionState.FAILED,
                idempotency_key=key,
                updated_at=now,
                error_code=exc.code.value,
                error_message=exc.message,
            )
        except (RuntimeError, ValueError) as exc:
            return ProvisioningOutcome(
                action_id=action.action_id,
                state=ProvisioningActionState.FAILED,
                idempotency_key=key,
                updated_at=now,
                error_code=ErrorCode.CONFLICT.value,
                error_message=str(exc),
            )
        return ProvisioningOutcome(
            action_id=action.action_id,
            state=ProvisioningActionState.COMPLETED,
            idempotency_key=key,
            updated_at=now,
        )

    def _catalog(self, components: tuple[DiscoveredComponent, ...]) -> list[JsonValue]:
        catalog: list[JsonValue] = [self._component_card(component) for component in components]
        registry = self.registry
        if registry is not None and registry.enabled:
            try:
                items = registry.search()
            except RuntimeError:
                items = ()
            catalog.extend(self._registry_card(item) for item in items)
        return catalog

    def _component_card(self, component: DiscoveredComponent) -> dict[str, JsonValue]:
        compatibility = self.component_setup.resolver.resolve(
            component,
            self.component_setup.environment(),
        )
        local = component.source_ref is None or not component.source_ref.startswith("external:")
        install_status = (
            "installed"
            if component.availability is ComponentAvailability.AVAILABLE
            else "installable"
            if component.availability is ComponentAvailability.INSTALLATION_REQUIRED
            and _registry_source(component.source_ref) is not None
            and self.registry is not None
            and self.registry.mutation_enabled
            else "manual_required"
            if component.availability is ComponentAvailability.INSTALLATION_REQUIRED
            else "blocked"
        )
        return {
            "id": component.component_id,
            "kind": "component",
            "display_name": component.display_name,
            "utility": _category_utility(component.category),
            "category": component.category.value,
            "install_status": install_status,
            "compatibility": compatibility.state.value,
            "recommendation": component.lifecycle.value,
            "dependencies": [
                requirement.capability
                for requirement in component.requirements
                if requirement.required
            ],
            "blockers": list(compatibility.reasons),
            "delivery": "local" if local else "external",
            "requires_secrets": False,
            "configuration_fields": [],
            "license": None,
            "upstream": component.source_ref,
            "version": component.version,
            "technical_id": component.component_id,
        }

    def _registry_card(self, item: SetupRegistryItem) -> dict[str, JsonValue]:
        registry = self.registry
        return project_registry_card(
            item,
            installed_version=(
                registry.installed_version(item.item_id) if registry is not None else None
            ),
            mutation_enabled=registry is not None and registry.mutation_enabled,
        )

    def _steps(
        self,
        session: SetupSessionRecord,
        profile: SetupProfile | None,
        plan: tuple[_PlanAction, ...],
        onboarding_status: dict[str, JsonValue],
    ) -> tuple[tuple[SetupStep, SetupStepState], ...]:
        model_ready = any(
            isinstance(value := onboarding_status.get(key), int) and value > 0
            for key in ("local_model_count", "self_hosted_model_count")
        )
        blockers = _plan_blocked(plan)
        ready = (
            onboarding_status.get("state") == "ready_for_task"
            and profile is not None
            and not blockers
        )
        completed = {
            SetupStep.IDENTITY,
            SetupStep.ENVIRONMENT,
        }
        if profile is not None:
            completed.add(SetupStep.COMPONENTS)
        if model_ready:
            completed.add(SetupStep.CONFIGURATION)
        if profile is not None and not blockers:
            completed.add(SetupStep.VALIDATION)
        if ready:
            completed.add(SetupStep.READY)
        result: list[tuple[SetupStep, SetupStepState]] = []
        for step in SetupStep:
            if step in completed:
                state = SetupStepState.COMPLETE
            elif blockers and step in {SetupStep.VALIDATION, SetupStep.READY}:
                state = SetupStepState.BLOCKED
            elif step is session.current_step:
                state = SetupStepState.CURRENT
            else:
                state = SetupStepState.PENDING
            result.append((step, state))
        return tuple(result)

    def _validate_registry_selections(self, selections: tuple[RegistrySelection, ...]) -> None:
        if not selections:
            return
        registry = self.registry
        if registry is None or not registry.enabled:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry selections require an explicitly configured Registry",
            )
        for selection in selections:
            try:
                item = registry.get(selection.item_id, selection.version)
            except LookupError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
            except RuntimeError as exc:
                raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
            if item.deprecated or item.yanked:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"registry item cannot be selected for first-run setup: {selection.ref}",
                )

    @staticmethod
    def _require_resource(resource_ref: str) -> None:
        if resource_ref != SETUP_SESSION_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"setup lifecycle requires resource_ref={SETUP_SESSION_RESOURCE_ID!r}",
            )


def _session_from_json(value: object) -> SetupSessionRecord:
    if not isinstance(value, dict):
        raise ValueError("setup session must be an object")
    principal_ref = value.get("principal_ref")
    current_step = value.get("current_step")
    updated_at = value.get("updated_at")
    if not isinstance(principal_ref, str) or not principal_ref.strip():
        raise ValueError("setup session principal_ref must be non-blank")
    if not isinstance(current_step, str):
        raise ValueError("setup session current_step must be a string")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise ValueError("setup session updated_at must be non-blank")
    raw_registry = value.get("registry_items", [])
    if not isinstance(raw_registry, list):
        raise ValueError("setup session registry_items must be a list")
    registry_items = tuple(_registry_selection_from_json(item) for item in raw_registry)
    raw_outcomes = value.get("outcomes", [])
    if not isinstance(raw_outcomes, list):
        raise ValueError("setup session outcomes must be a list")
    outcomes = tuple(_outcome_from_json(item) for item in raw_outcomes)
    return SetupSessionRecord(
        principal_ref=principal_ref,
        current_step=SetupStep(current_step),
        registry_items=registry_items,
        outcomes=outcomes,
        updated_at=updated_at,
    )


def _registry_selection_from_json(value: object) -> RegistrySelection:
    if not isinstance(value, dict):
        raise ValueError("registry setup selection must be an object")
    item_id = value.get("item_id")
    version = value.get("version")
    if not isinstance(item_id, str) or not isinstance(version, str):
        raise ValueError("registry setup selection fields must be strings")
    return RegistrySelection(item_id=item_id, version=version)


def _outcome_from_json(value: object) -> ProvisioningOutcome:
    if not isinstance(value, dict):
        raise ValueError("provisioning outcome must be an object")
    required = ("action_id", "state", "idempotency_key", "updated_at")
    if any(not isinstance(value.get(key), str) for key in required):
        raise ValueError("provisioning outcome required fields must be strings")
    error_code = value.get("error_code")
    error_message = value.get("error_message")
    if error_code is not None and not isinstance(error_code, str):
        raise ValueError("provisioning outcome error_code must be a string or null")
    if error_message is not None and not isinstance(error_message, str):
        raise ValueError("provisioning outcome error_message must be a string or null")
    return ProvisioningOutcome(
        action_id=cast(str, value["action_id"]),
        state=ProvisioningActionState(cast(str, value["state"])),
        idempotency_key=cast(str, value["idempotency_key"]),
        updated_at=cast(str, value["updated_at"]),
        error_code=error_code,
        error_message=error_message,
    )


def _registry_selections(value: JsonValue) -> tuple[RegistrySelection, ...]:
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "registry_items must be a list")
    selections: list[RegistrySelection] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "registry item selections must be objects"
            )
        item_id = raw.get("item_id")
        version = raw.get("version")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "registry item_id must be non-blank")
        if not isinstance(version, str) or not version.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "registry version must be non-blank")
        selection = RegistrySelection(item_id=item_id, version=version)
        if selection.ref in seen:
            raise ContractError(ErrorCode.INVALID_REQUEST, "duplicate registry setup selection")
        seen.add(selection.ref)
        selections.append(selection)
    return tuple(sorted(selections, key=lambda item: item.ref))


def _action_ids(value: JsonValue | None) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "action_ids must be a list of non-blank strings"
        )
    return set(cast(list[str], value))


def _plan_blocked(plan: tuple[_PlanAction, ...]) -> bool:
    return any(
        action.state
        in {
            ProvisioningActionState.BLOCKED,
            ProvisioningActionState.MANUAL_REQUIRED,
            ProvisioningActionState.FAILED,
        }
        for action in plan
    )


def _registry_source(source_ref: str | None) -> tuple[str, str] | None:
    if source_ref is None or not source_ref.startswith("registry:"):
        return None
    raw = source_ref.removeprefix("registry:")
    item_id, separator, version = raw.rpartition("@")
    if not separator or not item_id.strip() or not version.strip():
        return None
    return item_id, version


def _action_registry_ref(action: _PlanAction) -> tuple[str, str]:
    raw = action.component_ref
    item_id, separator, version = raw.rpartition("@")
    if not separator or not item_id or not version:
        raise ContractError(ErrorCode.CONFLICT, "invalid registry setup action reference")
    return item_id, version


def _component_owner(component: DiscoveredComponent) -> str:
    if component.source_ref is None:
        return "platform"
    return component.source_ref.split(":", 1)[0]


def _category_utility(category: ComponentCategory) -> str:
    return {
        ComponentCategory.ORCHESTRATOR: "Coordinates agent and workflow execution.",
        ComponentCategory.MODEL_PROVIDER: "Provides model inference for agents and workflows.",
        ComponentCategory.EXECUTOR: "Runs isolated or local execution workloads.",
        ComponentCategory.MEMORY_KNOWLEDGE: "Stores or retrieves durable context and knowledge.",
        ComponentCategory.TOOLS_MCP: "Exposes tools, capabilities and MCP integrations.",
        ComponentCategory.STORAGE: "Persists files, artifacts and platform data.",
        ComponentCategory.COMPUTE: "Provides local or distributed compute capacity.",
    }[category]


def _reject_unknown_fields(payload: dict[str, JsonValue], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "setup payload contains unsupported fields",
            details={"unknown_fields": cast(list[JsonValue], unknown)},
        )


__all__ = [
    "BrowserFirstSetupService",
    "JsonSetupSessionStore",
    "ONBOARDING_PROVISION_SETUP_COMMAND",
    "ONBOARDING_UPDATE_SETUP_SESSION_COMMAND",
    "ONBOARDING_VALIDATE_SETUP_COMMAND",
    "ProvisioningActionKind",
    "ProvisioningActionState",
    "RegistrySelection",
    "SETUP_SESSION_RESOURCE_ID",
    "SetupStep",
    "SetupStepState",
]
