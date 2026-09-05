"""Durable JSON persistence for canonical capability-assignment policy."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue

from .codec import policy_from_json, policy_to_json, revision_from_json, revision_to_json
from .models import CapabilityAssignmentPolicy, CapabilityAssignmentRevision
from .repository import InMemoryCapabilityAssignmentRepository

CAPABILITY_ASSIGNMENT_REPOSITORY_SCHEMA_VERSION = "1"


class JsonCapabilityAssignmentRepository(InMemoryCapabilityAssignmentRepository):
    """Persist complete immutable histories with atomic whole-document replacement."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def create(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None:
        super().create(policy, revision)
        self._save()

    def append_revision(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None:
        super().append_revision(policy, revision)
        self._save()

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": CAPABILITY_ASSIGNMENT_REPOSITORY_SCHEMA_VERSION,
            "policies": [policy_to_json(item) for item in self.list()],
            "revisions": [
                revision_to_json(revision)
                for policy in self.list()
                for revision in self.list_revisions(policy.assignment_id)
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _restore(self) -> None:
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("capability-assignment repository must be an object")
        version = raw.get("schema_version")
        if version != CAPABILITY_ASSIGNMENT_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported capability-assignment repository schema version: "
                f"{version!r}; expected {CAPABILITY_ASSIGNMENT_REPOSITORY_SCHEMA_VERSION!r}"
            )
        policies_raw = raw.get("policies")
        revisions_raw = raw.get("revisions")
        if not isinstance(policies_raw, list) or not isinstance(revisions_raw, list):
            raise ValueError("capability-assignment policies/revisions must be arrays")
        policies = tuple(policy_from_json(value) for value in policies_raw)
        revisions = tuple(revision_from_json(value) for value in revisions_raw)
        histories: dict[str, list[CapabilityAssignmentRevision]] = {}
        for revision in revisions:
            histories.setdefault(revision.assignment_id, []).append(revision)
        for policy in policies:
            history = sorted(
                histories.pop(policy.assignment_id, []),
                key=lambda item: item.revision,
            )
            self._restore_policy(policy, history)
        if histories:
            raise ValueError("capability-assignment repository has revisions without policies")

    def _restore_policy(
        self,
        policy: CapabilityAssignmentPolicy,
        history: list[CapabilityAssignmentRevision],
    ) -> None:
        if not history or history[-1].revision != policy.current_revision:
            raise ValueError("capability-assignment policy does not match revision history")
        if [item.revision for item in history] != list(
            range(1, policy.current_revision + 1)
        ):
            raise ValueError("capability-assignment revision history is not contiguous")
        for index, revision in enumerate(history):
            interim = replace(policy, current_revision=revision.revision)
            if index == 0:
                InMemoryCapabilityAssignmentRepository.create(self, interim, revision)
            else:
                InMemoryCapabilityAssignmentRepository.append_revision(
                    self,
                    interim,
                    revision,
                )
        if self.get(policy.assignment_id) != policy:
            raise ValueError("restored capability-assignment policy metadata differs")
