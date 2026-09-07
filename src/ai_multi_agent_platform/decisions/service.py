"""Decision Record application service and evidence/action boundaries (#598)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    DecisionOutcome,
    DecisionRecord,
    DecisionRecordView,
    DecisionReference,
    DecisionStatus,
)
from .repository import DecisionRepository

ReferenceResolver = Callable[[DecisionReference], bool]


class DecisionReferenceValidator:
    """Optional exact-reference validation without owning referenced domains."""

    def __init__(self, resolvers: dict[str, ReferenceResolver] | None = None) -> None:
        self._resolvers = dict(resolvers or {})

    def validate(self, reference: DecisionReference) -> None:
        resolver = self._resolvers.get(reference.kind)
        if resolver is not None and not resolver(reference):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "DecisionRecord references an unavailable or mismatched canonical resource",
                details={"kind": reference.kind, "resource_id": reference.resource_id},
            )


class DecisionService:
    """Own immutable decisions while leaving authorization and resource activation external."""

    def __init__(
        self,
        repository: DecisionRepository,
        *,
        reference_validator: DecisionReferenceValidator | None = None,
    ) -> None:
        self.repository = repository
        self.reference_validator = reference_validator or DecisionReferenceValidator()

    def create(self, record: DecisionRecord) -> DecisionRecordView:
        if record.status is not DecisionStatus.CURRENT:
            raise ContractError(ErrorCode.INVALID_REQUEST, "new DecisionRecord must be current")
        if record.supersedes is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement decisions must use the supersede operation",
            )
        self._validate_references(record)
        self.repository.create(record)
        return self.view(record.id)

    def supersede(self, previous_id: str, replacement: DecisionRecord) -> DecisionRecordView:
        previous = self.repository.get(previous_id)
        previous_view = self.view(previous_id)
        if previous_view.status is not DecisionStatus.CURRENT:
            raise ContractError(ErrorCode.CONFLICT, "only a current DecisionRecord can be superseded")
        if replacement.status is not DecisionStatus.CURRENT:
            raise ContractError(ErrorCode.INVALID_REQUEST, "replacement DecisionRecord must be current")
        if replacement.supersedes != previous_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement DecisionRecord must reference the record it supersedes",
            )
        if (replacement.scope_type, replacement.scope_id) != (previous.scope_type, previous.scope_id):
            raise ContractError(ErrorCode.INVALID_REQUEST, "supersession must preserve decision scope")
        if replacement.subject != previous.subject:
            raise ContractError(ErrorCode.INVALID_REQUEST, "supersession must preserve decision subject")
        if not _same_subject_reference(replacement.subject_ref, previous.subject_ref):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "supersession must preserve exact subject resource identity",
            )
        self._validate_references(replacement)
        self.repository.create_superseding(previous_id, replacement)
        return self.view(replacement.id)

    def withdraw(self, decision_record_id: str, *, actor_ref: str, reason: str) -> DecisionRecordView:
        current = self.view(decision_record_id)
        if current.status is not DecisionStatus.CURRENT:
            raise ContractError(ErrorCode.CONFLICT, "only a current DecisionRecord can be withdrawn")
        self.repository.withdraw(decision_record_id, actor_ref=actor_ref, reason=reason)
        return self.view(decision_record_id)

    def link_downstream_provenance(
        self,
        decision_record_id: str,
        reference: DecisionReference,
    ) -> DecisionRecordView:
        """Append provenance only; this method deliberately cannot activate the target resource."""

        current = self.view(decision_record_id)
        if current.status is not DecisionStatus.CURRENT:
            raise ContractError(
                ErrorCode.CONFLICT,
                "superseded or withdrawn DecisionRecord cannot drive new downstream provenance",
            )
        if current.record.outcome not in {
            DecisionOutcome.ADOPT,
            DecisionOutcome.EXPERIMENTAL,
            DecisionOutcome.CUSTOM,
        }:
            raise ContractError(
                ErrorCode.CONFLICT,
                "rejected/deferred DecisionRecord cannot drive downstream action provenance",
            )
        self.reference_validator.validate(reference)
        self.repository.add_downstream_ref(decision_record_id, reference)
        return self.view(decision_record_id)

    def action_provenance(self, decision_record_id: str) -> dict[str, JsonValue]:
        """Return safe metadata an owner-domain action may persist after its own policy gates."""

        current = self.view(decision_record_id)
        if current.status is not DecisionStatus.CURRENT:
            raise ContractError(ErrorCode.CONFLICT, "DecisionRecord is no longer current")
        return {
            "decision_record_id": current.record.id,
            "decision_digest": current.record.content_digest,
            "decision_outcome": current.record.outcome.value,
        }

    def view(self, decision_record_id: str) -> DecisionRecordView:
        record = self.repository.get(decision_record_id)
        successor = self.repository.superseded_by(decision_record_id)
        withdrawal = self.repository.withdrawal(decision_record_id)
        status = DecisionStatus.CURRENT
        withdrawn_at = None
        withdrawal_reason = None
        if successor is not None:
            status = DecisionStatus.SUPERSEDED
        elif withdrawal is not None:
            status = DecisionStatus.WITHDRAWN
            withdrawn_at, withdrawal_reason = withdrawal
        return DecisionRecordView(
            record=record,
            status=status,
            superseded_by=successor,
            withdrawn_at=withdrawn_at,
            withdrawal_reason=withdrawal_reason,
            downstream_refs=self.repository.downstream_refs(decision_record_id),
        )

    def list_views(self) -> tuple[DecisionRecordView, ...]:
        return tuple(self.view(record.id) for record in self.repository.list())

    def supersession_chain(self, decision_record_id: str) -> tuple[DecisionRecordView, ...]:
        """Return the complete predecessor-to-successor chain containing this decision."""

        records = {record.id: record for record in self.repository.list()}
        if decision_record_id not in records:
            raise ContractError(ErrorCode.NOT_FOUND, "DecisionRecord was not found")
        predecessor_by_successor = {
            record.id: record.supersedes for record in records.values() if record.supersedes is not None
        }
        root = decision_record_id
        seen: set[str] = set()
        while root in predecessor_by_successor:
            if root in seen:
                raise ContractError(ErrorCode.CONFLICT, "DecisionRecord supersession cycle detected")
            seen.add(root)
            predecessor = predecessor_by_successor[root]
            assert predecessor is not None
            root = predecessor
        chain: list[DecisionRecordView] = []
        cursor: str | None = root
        while cursor is not None:
            if cursor in {item.record.id for item in chain}:
                raise ContractError(ErrorCode.CONFLICT, "DecisionRecord supersession cycle detected")
            view = self.view(cursor)
            chain.append(view)
            cursor = view.superseded_by
        return tuple(chain)

    def _validate_references(self, record: DecisionRecord) -> None:
        for reference in _all_references(record):
            self.reference_validator.validate(reference)


def _all_references(record: DecisionRecord) -> Iterable[DecisionReference]:
    for reference in (record.subject_ref, record.approval_ref, record.adr_ref):
        if reference is not None:
            yield reference
    for collection in (
        record.evidence_refs,
        record.evaluation_refs,
        record.finding_refs,
        record.cost_resource_refs,
    ):
        yield from collection
    for alternative in record.alternatives:
        if alternative.resource_ref is not None:
            yield alternative.resource_ref
        yield from alternative.evidence_refs


def _same_subject_reference(
    left: DecisionReference | None,
    right: DecisionReference | None,
) -> bool:
    if left is None or right is None:
        return left is right
    return (left.kind, left.resource_id) == (right.kind, right.resource_id)
