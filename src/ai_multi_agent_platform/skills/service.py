"""Canonical Skill Registry service and third-party trust lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .models import (
    SkillDefinition,
    SkillEvaluationStatus,
    SkillProfile,
    SkillRevision,
    SkillRevisionRef,
    SkillTrustStatus,
    new_skill_id,
)
from .repository import SkillRepository


_TRUST_TRANSITIONS: dict[SkillTrustStatus, frozenset[SkillTrustStatus]] = {
    SkillTrustStatus.DISCOVERED: frozenset(
        {
            SkillTrustStatus.SOURCE_VERIFIED,
            SkillTrustStatus.REJECTED,
            SkillTrustStatus.DEFERRED,
        }
    ),
    SkillTrustStatus.SOURCE_VERIFIED: frozenset(
        {
            SkillTrustStatus.SECURITY_REVIEWED,
            SkillTrustStatus.REJECTED,
            SkillTrustStatus.DEFERRED,
        }
    ),
    SkillTrustStatus.SECURITY_REVIEWED: frozenset(
        {
            SkillTrustStatus.PILOT,
            SkillTrustStatus.REJECTED,
            SkillTrustStatus.DEFERRED,
        }
    ),
    SkillTrustStatus.PILOT: frozenset(
        {
            SkillTrustStatus.ADOPTED,
            SkillTrustStatus.REJECTED,
            SkillTrustStatus.DEFERRED,
        }
    ),
    SkillTrustStatus.ADOPTED: frozenset({SkillTrustStatus.REJECTED, SkillTrustStatus.DEFERRED}),
    SkillTrustStatus.REJECTED: frozenset(),
    # DEFERRED deliberately forgets no review requirement in the safe direction:
    # resuming always restarts at source verification before later stages can be reached.
    SkillTrustStatus.DEFERRED: frozenset(
        {SkillTrustStatus.SOURCE_VERIFIED, SkillTrustStatus.REJECTED}
    ),
}


class SkillService:
    """Own stable Skill identities while preserving immutable historical revisions."""

    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository

    def create_skill(
        self,
        profile: SkillProfile,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        workspace_id: str | None = None,
        provenance: Provenance | None = None,
        skill_id: str | None = None,
    ) -> SkillRevision:
        self._validate_profile_for_registry(profile, creating=True)
        now = datetime.now(UTC)
        resolved_id = skill_id or new_skill_id()
        definition = SkillDefinition(
            skill_id=resolved_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            updated_at=now,
        )
        revision = SkillRevision(
            skill_id=resolved_id,
            revision=1,
            profile=profile,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            provenance=provenance,
        )
        self.repository.create_skill(definition, revision)
        return revision

    def update_skill(
        self,
        skill_id: str,
        profile: SkillProfile,
        *,
        expected_revision: int,
        owner_ref: OwnerRef | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        provenance: Provenance | None = None,
    ) -> SkillRevision:
        return self._update_skill(
            skill_id,
            profile,
            expected_revision=expected_revision,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            provenance=provenance,
            allow_review_state_change=False,
        )

    def _update_skill(
        self,
        skill_id: str,
        profile: SkillProfile,
        *,
        expected_revision: int,
        owner_ref: OwnerRef | None,
        project_id: str | None,
        workspace_id: str | None,
        provenance: Provenance | None,
        allow_review_state_change: bool,
    ) -> SkillRevision:
        current = self.repository.get_skill(skill_id)
        if current.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "skill revision changed",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        self._validate_profile_for_registry(profile, creating=False)
        previous = self.repository.get_skill_revision(skill_id, expected_revision)
        self._validate_trust_update(
            previous.profile,
            profile,
            allow_review_state_change=allow_review_state_change,
        )
        now = datetime.now(UTC)
        resolved_owner = owner_ref or current.owner_ref
        next_revision = expected_revision + 1
        definition = replace(
            current,
            owner_ref=resolved_owner,
            current_revision=next_revision,
            project_id=project_id,
            workspace_id=workspace_id,
            updated_at=now,
        )
        revision = SkillRevision(
            skill_id=skill_id,
            revision=next_revision,
            profile=profile,
            owner_ref=resolved_owner,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            provenance=provenance,
        )
        self.repository.update_skill(definition, revision)
        return revision

    def get_skill_revision(self, skill_id: str, revision: int | None = None) -> SkillRevision:
        definition = self.repository.get_skill(skill_id)
        resolved_revision = definition.current_revision if revision is None else revision
        return self.repository.get_skill_revision(skill_id, resolved_revision)

    def list_skills(
        self,
        *,
        query: str | None = None,
        purpose_category: str | None = None,
        include_disabled: bool = True,
        include_deprecated: bool = True,
    ) -> tuple[SkillRevision, ...]:
        needle = query.casefold().strip() if query is not None else None
        matches: list[SkillRevision] = []
        for definition in self.repository.list_skills():
            revision = self.get_skill_revision(definition.skill_id)
            profile = revision.profile
            if not include_disabled and not profile.enabled:
                continue
            if not include_deprecated and profile.deprecated:
                continue
            if purpose_category is not None and purpose_category not in profile.purpose_categories:
                continue
            if needle and needle not in f"{profile.name} {profile.description}".casefold():
                continue
            matches.append(revision)
        return tuple(
            sorted(matches, key=lambda item: (item.profile.name.casefold(), item.skill_id))
        )

    def set_enabled(
        self,
        skill_id: str,
        enabled: bool,
        *,
        expected_revision: int,
        provenance: Provenance | None = None,
    ) -> SkillRevision:
        current = self.get_skill_revision(skill_id, expected_revision)
        if enabled and current.profile.source is not None:
            if current.profile.trust_status is not SkillTrustStatus.ADOPTED:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "third-party skill cannot be enabled before explicit adoption",
                )
            if current.profile.evaluation_status is not SkillEvaluationStatus.PASSED:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "third-party skill cannot be enabled before successful evaluation",
                )
        profile = replace(current.profile, enabled=enabled)
        return self.update_skill(
            skill_id,
            profile,
            expected_revision=expected_revision,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=provenance,
        )

    def deprecate_skill(
        self,
        skill_id: str,
        *,
        expected_revision: int,
        replacement: SkillRevisionRef | None = None,
        provenance: Provenance | None = None,
    ) -> SkillRevision:
        current = self.get_skill_revision(skill_id, expected_revision)
        if replacement is not None:
            self.repository.get_skill_revision(replacement.skill_id, replacement.revision)
            if replacement.skill_id == skill_id and replacement.revision == expected_revision:
                raise ContractError(ErrorCode.INVALID_REQUEST, "skill cannot replace itself")
        profile = replace(current.profile, deprecated=True, replacement=replacement)
        return self.update_skill(
            skill_id,
            profile,
            expected_revision=expected_revision,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=provenance,
        )

    def clone_skill(
        self,
        skill_id: str,
        *,
        revision: int | None = None,
        owner_ref: OwnerRef | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        name: str | None = None,
        provenance: Provenance | None = None,
    ) -> SkillRevision:
        source = self.get_skill_revision(skill_id, revision)
        profile = replace(
            source.profile,
            name=name or source.profile.name,
            enabled=False if source.profile.source is not None else source.profile.enabled,
            trust_status=(
                SkillTrustStatus.DISCOVERED
                if source.profile.source is not None
                else source.profile.trust_status
            ),
            evaluation_status=(
                SkillEvaluationStatus.NOT_EVALUATED
                if source.profile.source is not None
                else source.profile.evaluation_status
            ),
            evaluation_metadata=(
                {} if source.profile.source is not None else source.profile.evaluation_metadata
            ),
        )
        return self.create_skill(
            profile,
            owner_ref=owner_ref or source.owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            provenance=provenance,
        )

    def transition_trust(
        self,
        skill_id: str,
        target: SkillTrustStatus,
        *,
        expected_revision: int,
        evaluation_status: SkillEvaluationStatus | None = None,
        evaluation_metadata: Mapping[str, JsonValue] | None = None,
        provenance: Provenance | None = None,
    ) -> SkillRevision:
        current = self.get_skill_revision(skill_id, expected_revision)
        if current.profile.source is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "trust review lifecycle is only required for third-party skills",
            )
        allowed = _TRUST_TRANSITIONS[current.profile.trust_status]
        if target not in allowed:
            raise ContractError(
                ErrorCode.CONFLICT,
                "invalid third-party skill trust transition",
                details={
                    "current": current.profile.trust_status.value,
                    "target": target.value,
                },
            )
        next_evaluation = evaluation_status or current.profile.evaluation_status
        next_metadata = (
            current.profile.evaluation_metadata
            if evaluation_metadata is None
            else evaluation_metadata
        )
        if (
            target is SkillTrustStatus.ADOPTED
            and next_evaluation is not SkillEvaluationStatus.PASSED
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "third-party skill adoption requires a passed evaluation",
            )
        profile = replace(
            current.profile,
            trust_status=target,
            evaluation_status=next_evaluation,
            evaluation_metadata=next_metadata,
            enabled=False if target is not SkillTrustStatus.ADOPTED else current.profile.enabled,
        )
        return self._update_skill(
            skill_id,
            profile,
            expected_revision=expected_revision,
            owner_ref=None,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=provenance,
            allow_review_state_change=True,
        )

    @staticmethod
    def _validate_profile_for_registry(profile: SkillProfile, *, creating: bool) -> None:
        if profile.source is None:
            return
        if creating and profile.trust_status is not SkillTrustStatus.DISCOVERED:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "new third-party skill must enter the registry as discovered",
            )
        if profile.trust_status is not SkillTrustStatus.ADOPTED and profile.enabled:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "untrusted third-party skill must remain disabled",
            )
        if (
            profile.trust_status is SkillTrustStatus.ADOPTED
            and profile.evaluation_status is not SkillEvaluationStatus.PASSED
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "adopted third-party skill requires a passed evaluation",
            )

    @staticmethod
    def _validate_trust_update(
        previous: SkillProfile,
        updated: SkillProfile,
        *,
        allow_review_state_change: bool,
    ) -> None:
        if previous.source != updated.source:
            if previous.source is not None or updated.source is not None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "third-party source provenance cannot be rewritten in-place; clone instead",
                )
        review_state_changed = (
            previous.trust_status != updated.trust_status
            or previous.evaluation_status != updated.evaluation_status
            or previous.evaluation_metadata != updated.evaluation_metadata
        )
        if review_state_changed and not allow_review_state_change:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Skill trust/evaluation state may only change through the explicit review lifecycle",
            )
        if previous.trust_status != updated.trust_status:
            allowed = _TRUST_TRANSITIONS[previous.trust_status]
            if updated.trust_status not in allowed:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "skill trust status must follow the explicit review lifecycle",
                )
