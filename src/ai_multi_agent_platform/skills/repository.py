"""Persistence boundary and deterministic reference repository for canonical Skills."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import (
    SkillBundle,
    SkillDefinition,
    SkillRevision,
    SkillRunBinding,
)


class SkillRepository(Protocol):
    def create_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None: ...

    def update_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None: ...

    def get_skill(self, skill_id: str) -> SkillDefinition: ...

    def list_skills(self) -> tuple[SkillDefinition, ...]: ...

    def get_skill_revision(self, skill_id: str, revision: int) -> SkillRevision: ...

    def list_skill_revisions(self, skill_id: str) -> tuple[SkillRevision, ...]: ...

    def save_bundle(self, bundle: SkillBundle) -> None: ...

    def get_bundle(self, skill_bundle_id: str) -> SkillBundle: ...

    def list_bundles(self, run_id: str | None = None) -> tuple[SkillBundle, ...]: ...

    def create_binding(self, binding: SkillRunBinding) -> None: ...

    def bind_agent_run(self, binding_id: str, agent_run_id: str) -> SkillRunBinding: ...

    def get_binding(self, binding_id: str) -> SkillRunBinding: ...

    def list_bindings(self, run_id: str | None = None) -> tuple[SkillRunBinding, ...]: ...


class InMemorySkillRepository:
    """Reference store preserving all revisions, bundles and historical bindings."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._revisions: dict[tuple[str, int], SkillRevision] = {}
        self._bundles: dict[str, SkillBundle] = {}
        self._bindings: dict[str, SkillRunBinding] = {}
        self._agent_run_binding: dict[str, str] = {}

    def create_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None:
        if definition.skill_id in self._skills:
            raise ContractError(ErrorCode.CONFLICT, f"skill already exists: {definition.skill_id}")
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new skill must start at revision 1")
        self._validate_pair(definition, revision)
        self._skills[definition.skill_id] = definition
        self._revisions[(revision.skill_id, revision.revision)] = revision

    def update_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None:
        current = self.get_skill(definition.skill_id)
        expected = current.current_revision + 1
        if definition.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "skill revision must increase exactly by one",
                details={"current_revision": current.current_revision, "new_revision": revision.revision},
            )
        self._validate_pair(definition, revision)
        key = (revision.skill_id, revision.revision)
        if key in self._revisions:
            raise ContractError(ErrorCode.CONFLICT, "skill revision already exists")
        self._revisions[key] = revision
        self._skills[definition.skill_id] = definition

    def get_skill(self, skill_id: str) -> SkillDefinition:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"skill not found: {skill_id}") from exc

    def list_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills[skill_id] for skill_id in sorted(self._skills))

    def get_skill_revision(self, skill_id: str, revision: int) -> SkillRevision:
        try:
            return self._revisions[(skill_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"skill revision not found: {skill_id}@{revision}",
            ) from exc

    def list_skill_revisions(self, skill_id: str) -> tuple[SkillRevision, ...]:
        self.get_skill(skill_id)
        revisions = [
            revision
            for (current_id, _), revision in self._revisions.items()
            if current_id == skill_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def save_bundle(self, bundle: SkillBundle) -> None:
        existing = self._bundles.get(bundle.skill_bundle_id)
        if existing is not None:
            if existing != bundle:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "skill bundle identity is immutable",
                )
            return
        for entry in bundle.entries:
            revision = self.get_skill_revision(entry.ref.skill_id, entry.ref.revision)
            if revision.ref != entry.ref:
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid skill bundle revision")
        self._bundles[bundle.skill_bundle_id] = bundle

    def get_bundle(self, skill_bundle_id: str) -> SkillBundle:
        try:
            return self._bundles[skill_bundle_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"skill bundle not found: {skill_bundle_id}",
            ) from exc

    def list_bundles(self, run_id: str | None = None) -> tuple[SkillBundle, ...]:
        bundles = list(self._bundles.values())
        if run_id is not None:
            bundles = [bundle for bundle in bundles if bundle.run_id == run_id]
        return tuple(sorted(bundles, key=lambda item: (item.created_at, item.skill_bundle_id)))

    def create_binding(self, binding: SkillRunBinding) -> None:
        if binding.binding_id in self._bindings:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"skill run binding already exists: {binding.binding_id}",
            )
        bundle = self.get_bundle(binding.skill_bundle_id)
        if bundle.digest != binding.skill_bundle_hash:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "skill run binding hash does not match immutable bundle",
            )
        if (
            bundle.run_id != binding.run_id
            or bundle.task_id != binding.task_id
            or bundle.agent_id != binding.agent_id
            or bundle.agent_revision != binding.agent_revision
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "skill run binding execution context does not match bundle",
            )
        if binding.agent_run_id is not None:
            self._claim_agent_run(binding.agent_run_id, binding.binding_id)
        self._bindings[binding.binding_id] = binding

    def bind_agent_run(self, binding_id: str, agent_run_id: str) -> SkillRunBinding:
        current = self.get_binding(binding_id)
        if current.agent_run_id is not None:
            if current.agent_run_id != agent_run_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "skill run binding is already pinned to a different AgentRun",
                )
            return current
        self._claim_agent_run(agent_run_id, binding_id)
        updated = replace(current, agent_run_id=agent_run_id)
        self._bindings[binding_id] = updated
        return updated

    def get_binding(self, binding_id: str) -> SkillRunBinding:
        try:
            return self._bindings[binding_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"skill run binding not found: {binding_id}",
            ) from exc

    def list_bindings(self, run_id: str | None = None) -> tuple[SkillRunBinding, ...]:
        bindings = list(self._bindings.values())
        if run_id is not None:
            bindings = [binding for binding in bindings if binding.run_id == run_id]
        return tuple(sorted(bindings, key=lambda item: (item.created_at, item.binding_id)))

    def _claim_agent_run(self, agent_run_id: str, binding_id: str) -> None:
        existing = self._agent_run_binding.get(agent_run_id)
        if existing is not None and existing != binding_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "AgentRun already has a different Skill Bundle binding",
                details={"agent_run_id": agent_run_id, "binding_id": existing},
            )
        self._agent_run_binding[agent_run_id] = binding_id

    @staticmethod
    def _validate_pair(definition: SkillDefinition, revision: SkillRevision) -> None:
        if definition.skill_id != revision.skill_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "skill definition/revision ID mismatch",
            )
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "skill definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.workspace_id != revision.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "skill definition ownership scope must match latest revision snapshot",
            )
