"""Single-node classification bridge for Verification-derived Context evidence.

The #86 Verification model intentionally binds exact subject/evidence identity but does not
persist an independent data-classification field. Context must therefore derive classification
from canonical owners without silently weakening sensitive findings.
"""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRepository, AgentRunStatus
from ai_multi_agent_platform.context.bindings import ContextRunBindingRepository
from ai_multi_agent_platform.context.classification import effective_context_bundle_classification
from ai_multi_agent_platform.context.models import ContextDataClassification
from ai_multi_agent_platform.context.resolver import ContextBundleRepository, ContextSourceRequest
from ai_multi_agent_platform.context.verification_source import (
    VerificationContextClassificationResolver,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    OperationContext,
    strongest_classification,
)
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileRecord
from ai_multi_agent_platform.verification import (
    VerificationEvidenceResolver,
    VerificationRequest,
    VerificationResult,
)


class CanonicalVerificationContextClassificationResolver(VerificationContextClassificationResolver):
    """Inherit exact persisted classifications and fail closed when provenance is incomplete."""

    def __init__(
        self,
        files: FileProvider,
        *,
        evidence: VerificationEvidenceResolver | None = None,
        agents: AgentRepository | None = None,
        bundles: ContextBundleRepository | None = None,
        run_bindings: ContextRunBindingRepository | None = None,
    ) -> None:
        self.files = files
        self.evidence = evidence
        self.agents = agents
        self.bundles = bundles
        self.run_bindings = run_bindings

    async def classify(
        self,
        source_request: ContextSourceRequest,
        verification_request: VerificationRequest,
        result: VerificationResult,
    ) -> ContextDataClassification:
        classifications: list[DataClassification] = []

        if result.subject.subject_type == "artifact":
            classifications.append(
                await self._artifact_classification(
                    source_request,
                    result.subject.subject_id,
                    expected_file_id=result.subject.revision,
                    expected_digest=result.subject.digest,
                )
            )
        else:
            classifications.append(
                await self._result_classification(
                    verification_request=verification_request,
                    result=result,
                )
            )

        for artifact_id in result.evidence_artifact_ids:
            if (
                result.subject.subject_type == "artifact"
                and artifact_id == result.subject.subject_id
            ):
                # The exact subject FileRecord revision/digest was already proven above.
                continue
            # #86 validates additional evidence artifacts when a result is submitted, but its
            # durable VerificationResult currently keeps only their Artifact IDs, not the exact
            # FileRecord revision/digest that was reviewed. A later Artifact-to-File relink must
            # therefore never justify rendering findings at a weaker current classification.
            # Keep findings reference-only until exact auxiliary evidence provenance is persisted.
            classifications.append(DataClassification.SECRET)

        strongest = strongest_classification(*classifications) or DataClassification.SECRET
        return _context_classification(strongest)

    async def _result_classification(
        self,
        *,
        verification_request: VerificationRequest,
        result: VerificationResult,
    ) -> DataClassification:
        """Inherit the exact producer Context Bundle classification for one Result.

        Result does not own a separate persisted classification today. The only safe weaker-than-
        secret classification is therefore the immutable Context Bundle actually bound to the
        canonical producer AgentRun, and only when no tool or Artifact output introduced an
        additional classification source. Every link in Result -> Verification -> producer
        AgentRun -> ContextRunBinding -> ContextBundle must agree. Missing or contradictory
        provenance remains reference-only rather than being inferred from the current
        repair/reviewer execution.
        """

        if (
            self.evidence is None
            or self.agents is None
            or self.bundles is None
            or self.run_bindings is None
        ):
            return DataClassification.SECRET
        if verification_request.subject != result.subject:
            return DataClassification.SECRET

        try:
            context = await self.evidence.resolve_context(
                task_id=verification_request.task_id,
                subject_type=result.subject.subject_type,
                subject_id=result.subject.subject_id,
            )
        except ContractError:
            return DataClassification.SECRET

        if (
            context.subject != result.subject
            or context.run_id is None
            or verification_request.run_id != context.run_id
            or verification_request.producer != context.producer
        ):
            return DataClassification.SECRET
        producer = context.producer
        if producer is None or producer.agent_id is None or producer.agent_revision is None:
            return DataClassification.SECRET

        records = tuple(
            record
            for record in self.agents.list_agent_runs(context.run_id)
            if record.task_id == verification_request.task_id
            and result.subject.subject_id in record.result_ids
        )
        if len(records) != 1:
            return DataClassification.SECRET
        record = records[0]
        if (
            record.status is not AgentRunStatus.SUCCEEDED
            or record.agent.agent_id != producer.agent_id
            or record.agent.revision != producer.agent_revision
            or record.selected_model_config_id != producer.model_config_id
            or record.selected_provider_id != producer.provider_id
        ):
            return DataClassification.SECRET

        # Capability output may carry data that is more sensitive than the input Context Bundle.
        # Until the Result owner persists the effective output classification, never infer a weaker
        # class for a Result that consumed tools or emitted Artifacts.
        if record.tool_invocation_refs or record.artifact_ids:
            return DataClassification.SECRET

        try:
            binding = self.run_bindings.get(record.agent_run_id)
        except KeyError:
            return DataClassification.SECRET
        if (
            binding.run_id != context.run_id
            or binding.task_id != verification_request.task_id
            or binding.agent_id != producer.agent_id
            or binding.agent_revision != producer.agent_revision
        ):
            return DataClassification.SECRET

        try:
            bundle = self.bundles.get(binding.context_bundle_id)
        except KeyError:
            return DataClassification.SECRET
        if (
            bundle.context_bundle_id != binding.context_bundle_id
            or bundle.digest != binding.context_bundle_digest
            or bundle.run_id != binding.run_id
            or bundle.task_id != binding.task_id
            or bundle.agent_id != binding.agent_id
            or bundle.agent_revision != binding.agent_revision
        ):
            return DataClassification.SECRET
        return effective_context_bundle_classification(bundle)

    async def _artifact_classification(
        self,
        request: ContextSourceRequest,
        artifact_id: str,
        *,
        expected_file_id: str | None = None,
        expected_digest: str | None = None,
    ) -> DataClassification:
        operation = getattr(request, "operation", None)
        actor_ref = getattr(request, "actor_ref", None)
        if not isinstance(operation, OperationContext):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Verification Context classification requires OperationContext",
            )
        if not isinstance(actor_ref, str) or not actor_ref.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Verification Context classification requires actor_ref",
            )
        access = DataAccessContext(
            operation=operation,
            actor_ref=actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        linked = [
            record
            for record in await self.files.list_files(access)
            if artifact_id in record.artifact_ids
        ]
        if len(linked) != 1:
            # Missing or ambiguous owner evidence cannot justify rendering reviewer prose inline.
            return DataClassification.SECRET
        record = linked[0]
        if expected_file_id is not None and record.file_id != expected_file_id:
            # The reviewed Artifact revision must still resolve to the exact canonical FileRecord
            # that Verification bound. A same-ID Artifact projection is not sufficient evidence.
            return DataClassification.SECRET
        if expected_digest is not None and f"sha256:{record.sha256}" != expected_digest:
            # Never inherit a weaker classification from content that no longer matches the
            # immutable digest captured by the Verification subject.
            return DataClassification.SECRET
        return _persisted_file_classification(record)


def _persisted_file_classification(record: FileRecord) -> DataClassification:
    """Resolve the strongest persisted File classification without transient caller assumptions."""

    values: list[DataClassification] = []
    if record.classification is not None:
        try:
            values.append(
                record.classification
                if isinstance(record.classification, DataClassification)
                else DataClassification(record.classification)
            )
        except ValueError:
            return DataClassification.SECRET

    raw_metadata = record.metadata.get("data_classification")
    if raw_metadata is not None:
        # Match the canonical File egress contract: malformed classification metadata cannot be
        # ignored in favor of a weaker record field.
        if not isinstance(raw_metadata, str):
            return DataClassification.SECRET
        try:
            values.append(DataClassification(raw_metadata))
        except ValueError:
            return DataClassification.SECRET

    return strongest_classification(*values) or DataClassification.SECRET


def _context_classification(value: DataClassification) -> ContextDataClassification:
    if value is DataClassification.PUBLIC:
        return ContextDataClassification.PUBLIC
    if value is DataClassification.INTERNAL:
        return ContextDataClassification.INTERNAL
    if value in {DataClassification.CONFIDENTIAL, DataClassification.PRIVATE}:
        return ContextDataClassification.CONFIDENTIAL
    if value is DataClassification.RESTRICTED:
        return ContextDataClassification.RESTRICTED
    return ContextDataClassification.SECRET_REFERENCE


__all__ = ["CanonicalVerificationContextClassificationResolver"]
