"""Versioned restart-safe JSON persistence for canonical Skill state."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .codec import (
    skill_binding_from_json,
    skill_binding_to_json,
    skill_bundle_from_json,
    skill_bundle_to_json,
    skill_definition_from_json,
    skill_definition_to_json,
    skill_revision_from_json,
    skill_revision_to_json,
)
from .models import SkillBundle, SkillDefinition, SkillRevision, SkillRunBinding
from .repository import InMemorySkillRepository

SKILL_REPOSITORY_SCHEMA_VERSION = "1"


class JsonSkillRepository(InMemorySkillRepository):
    """Reference durable repository preserving exact revisions, bundles and bindings."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._load()

    def create_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None:
        super().create_skill(definition, revision)
        self._flush()

    def update_skill(self, definition: SkillDefinition, revision: SkillRevision) -> None:
        super().update_skill(definition, revision)
        self._flush()

    def delete_skill(self, skill_id: str) -> None:
        super().delete_skill(skill_id)
        self._flush()

    def save_bundle(self, bundle: SkillBundle) -> None:
        before = len(self.list_bundles())
        super().save_bundle(bundle)
        if len(self.list_bundles()) != before:
            self._flush()

    def delete_bundle(self, skill_bundle_id: str) -> None:
        super().delete_bundle(skill_bundle_id)
        self._flush()

    def create_binding(self, binding: SkillRunBinding) -> None:
        super().create_binding(binding)
        self._flush()

    def bind_agent_run(self, binding_id: str, agent_run_id: str) -> SkillRunBinding:
        before = self.get_binding(binding_id)
        updated = super().bind_agent_run(binding_id, agent_run_id)
        if updated != before:
            self._flush()
        return updated

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "failed to load Skill repository",
                details={"path": str(self.path), "reason": str(exc)},
            ) from exc
        if not isinstance(raw, dict):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Skill repository document must be an object",
            )
        document = cast(dict[str, object], raw)
        if document.get("schema_version") != SKILL_REPOSITORY_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "unsupported Skill repository schema version",
                details={"schema_version": cast(str | None, document.get("schema_version"))},
            )

        skills = _array(document.get("skills", []), "skills")
        for item in skills:
            entry = _object(item, "Skill repository entry")
            definition = skill_definition_from_json(entry.get("definition"))
            revisions = tuple(
                skill_revision_from_json(revision)
                for revision in _array(entry.get("revisions", []), "Skill revisions")
            )
            self._restore_skill(definition, revisions)

        for item in _array(document.get("bundles", []), "Skill Bundles"):
            super().save_bundle(skill_bundle_from_json(item))
        for item in _array(document.get("bindings", []), "Skill Run bindings"):
            super().create_binding(skill_binding_from_json(item))

    def _restore_skill(
        self,
        definition: SkillDefinition,
        revisions: tuple[SkillRevision, ...],
    ) -> None:
        if not revisions:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "persisted Skill requires revision history",
                details={"skill_id": definition.skill_id},
            )
        expected = tuple(range(1, definition.current_revision + 1))
        actual = tuple(revision.revision for revision in revisions)
        if actual != expected:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "persisted Skill revisions must be contiguous from revision 1",
                details={"skill_id": definition.skill_id},
            )
        first_definition = (
            definition
            if definition.current_revision == 1
            else replace(
                definition,
                current_revision=1,
                updated_at=revisions[0].created_at,
            )
        )
        super().create_skill(first_definition, revisions[0])
        for revision in revisions[1:]:
            next_definition = (
                definition
                if revision.revision == definition.current_revision
                else replace(
                    definition,
                    current_revision=revision.revision,
                    updated_at=revision.created_at,
                )
            )
            super().update_skill(next_definition, revision)

    def _flush(self) -> None:
        document = {
            "schema_version": SKILL_REPOSITORY_SCHEMA_VERSION,
            "skills": [
                {
                    "definition": skill_definition_to_json(definition),
                    "revisions": [
                        skill_revision_to_json(revision)
                        for revision in self.list_skill_revisions(definition.skill_id)
                    ],
                }
                for definition in self.list_skills()
            ],
            "bundles": [skill_bundle_to_json(bundle) for bundle in self.list_bundles()],
            "bindings": [skill_binding_to_json(binding) for binding in self.list_bindings()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an array")
    return cast(list[object], value)
