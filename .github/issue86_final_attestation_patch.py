from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text()
    if old not in text:
        raise SystemExit(f"anchor missing in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1))


def append_once(path: str, marker: str, addition: str) -> None:
    target = ROOT / path
    text = target.read_text()
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n")


# models.py: risk-class-selective self-verification is a versioned policy fact.
replace_once(
    "src/ai_multi_agent_platform/verification/models.py",
    "from ai_multi_agent_platform.domain import Provenance, new_id, validate_id\n",
    "from ai_multi_agent_platform.domain import Provenance, new_id, validate_id\n"
    "from ai_multi_agent_platform.security.authorization import RiskClassification\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/models.py",
    "    human_reviewer_must_differ: bool = False\n"
    "    forbid_self_verification: bool = False\n"
    "    require_distinct_verifiers: bool = False\n",
    "    human_reviewer_must_differ: bool = False\n"
    "    forbid_self_verification: bool = False\n"
    "    forbid_self_verification_risk_classes: tuple[RiskClassification, ...] = ()\n"
    "    require_distinct_verifiers: bool = False\n\n"
    "    def __post_init__(self) -> None:\n"
    "        if len(set(self.forbid_self_verification_risk_classes)) != len(\n"
    "            self.forbid_self_verification_risk_classes\n"
    "        ):\n"
    "            raise ValueError(\n"
    "                \"self-verification risk classes must be unique\"\n"
    "            )\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/models.py",
    "    scope: VerificationScope = VerificationScope()\n"
    "    independence: ReviewerIndependence = ReviewerIndependence()\n"
    "    max_repair_attempts: int = 0\n",
    "    scope: VerificationScope = VerificationScope()\n"
    "    independence: ReviewerIndependence = ReviewerIndependence()\n"
    "    risk_classification: RiskClassification = RiskClassification.STANDARD\n"
    "    max_repair_attempts: int = 0\n",
)

# service.py: strict production result submission + risk-selective self-verification.
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    "_CANONICAL_SUBJECT_TOKEN = object()\n",
    "_CANONICAL_SUBJECT_TOKEN = object()\n_CANONICAL_RESULT_TOKEN = object()\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    "    def __init__(self, *, require_canonical_subjects: bool = False) -> None:\n"
    "        self._require_canonical_subjects = require_canonical_subjects\n",
    "    def __init__(\n"
    "        self,\n"
    "        *,\n"
    "        require_canonical_subjects: bool = False,\n"
    "        require_canonical_results: bool = False,\n"
    "    ) -> None:\n"
    "        self._require_canonical_subjects = require_canonical_subjects\n"
    "        self._require_canonical_results = require_canonical_results\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    "    def submit_result(self, result: VerificationResult) -> VerificationResult:\n"
    "        request = self.get_request(result.verification_id)\n",
    "    def submit_result(\n"
    "        self,\n"
    "        result: VerificationResult,\n"
    "        *,\n"
    "        _canonical_result_token: object | None = None,\n"
    "    ) -> VerificationResult:\n"
    "        if (\n"
    "            self._require_canonical_results\n"
    "            and _canonical_result_token is not _CANONICAL_RESULT_TOKEN\n"
    "        ):\n"
    "            raise ContractError(\n"
    "                ErrorCode.FORBIDDEN,\n"
    "                \"raw verification results are disabled; use CanonicalVerificationRuntime\",\n"
    "            )\n"
    "        request = self.get_request(result.verification_id)\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    "        requires_producer = (\n"
    "            (rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT)\n"
    "            or rules.model_must_differ\n"
    "            or rules.provider_must_differ\n"
    "            or (rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN)\n"
    "            or rules.forbid_self_verification\n"
    "        )\n",
    "        risk_forbids_self = (\n"
    "            policy.risk_classification in rules.forbid_self_verification_risk_classes\n"
    "        )\n"
    "        requires_producer = (\n"
    "            (rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT)\n"
    "            or rules.model_must_differ\n"
    "            or rules.provider_must_differ\n"
    "            or (rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN)\n"
    "            or rules.forbid_self_verification\n"
    "            or risk_forbids_self\n"
    "        )\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    "        if rules.forbid_self_verification:\n"
    "            same_actor = verifier.verifier_ref == producer.actor_ref\n",
    "        if rules.forbid_self_verification or risk_forbids_self:\n"
    "            same_actor = verifier.verifier_ref == producer.actor_ref\n",
)

# persistence.py: persist risk enum and strict result configuration.
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    "from ai_multi_agent_platform.domain import Provenance\n",
    "from ai_multi_agent_platform.domain import Provenance\n"
    "from ai_multi_agent_platform.security.authorization import RiskClassification\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    "        VerificationRequestStatus,\n"
    "        VerifierKind,\n",
    "        VerificationRequestStatus,\n"
    "        VerifierKind,\n"
    "        RiskClassification,\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    "        require_canonical_subjects: bool = False,\n"
    "    ) -> None:\n"
    "        VerificationService.__init__(self, require_canonical_subjects=require_canonical_subjects)\n",
    "        require_canonical_subjects: bool = False,\n"
    "        require_canonical_results: bool = False,\n"
    "    ) -> None:\n"
    "        VerificationService.__init__(\n"
    "            self,\n"
    "            require_canonical_subjects=require_canonical_subjects,\n"
    "            require_canonical_results=require_canonical_results,\n"
    "        )\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    "    def submit_result(self, result: VerificationResult) -> VerificationResult:\n"
    "        submitted = super().submit_result(result)\n",
    "    def submit_result(\n"
    "        self,\n"
    "        result: VerificationResult,\n"
    "        *,\n"
    "        _canonical_result_token: object | None = None,\n"
    "    ) -> VerificationResult:\n"
    "        submitted = super().submit_result(\n"
    "            result, _canonical_result_token=_canonical_result_token\n"
    "        )\n",
)

# gate.py: output mutation invalidates the current completion subject without rewriting history.
replace_once(
    "src/ai_multi_agent_platform/verification/gate.py",
    "class CompletionAuthority(Protocol):\n"
    "    \"\"\"Synchronous deterministic authority consulted at canonical Task terminalization.\"\"\"\n\n"
    "    def assess_task_completion(self, task_id: str) -> CompletionGateDecision: ...\n\n\n"
    "class VerificationCompletionAuthority(CompletionAuthority):\n",
    "class CompletionAuthority(Protocol):\n"
    "    \"\"\"Synchronous deterministic authority consulted at canonical Task terminalization.\"\"\"\n\n"
    "    def assess_task_completion(self, task_id: str) -> CompletionGateDecision: ...\n\n\n"
    "@runtime_checkable\n"
    "class OutputChangeAwareCompletionAuthority(Protocol):\n"
    "    \"\"\"Optional hook for authorities whose acceptance binds to produced output.\"\"\"\n\n"
    "    def invalidate_task_subject(self, task_id: str) -> object: ...\n\n\n"
    "class VerificationCompletionAuthority(CompletionAuthority):\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/gate.py",
    "    def requirement_for(self, task_id: str) -> TaskVerificationRequirement | None:\n"
    "        validate_id(task_id, \"task\")\n"
    "        return self._requirements.get(task_id)\n\n"
    "    def bind_subject(\n",
    "    def requirement_for(self, task_id: str) -> TaskVerificationRequirement | None:\n"
    "        validate_id(task_id, \"task\")\n"
    "        return self._requirements.get(task_id)\n\n"
    "    def invalidate_task_subject(\n"
    "        self,\n"
    "        task_id: str,\n"
    "        *,\n"
    "        now: datetime | None = None,\n"
    "    ) -> TaskVerificationRequirement | None:\n"
    "        validate_id(task_id, \"task\")\n"
    "        requirement = self._requirements.get(task_id)\n"
    "        if requirement is None or requirement.subject is None:\n"
    "            return requirement\n"
    "        current = _require_aware(now or _utc_now(), \"verification invalidation time\")\n"
    "        updated = replace(requirement, subject=None, updated_at=current)\n"
    "        self._requirements[task_id] = updated\n"
    "        return updated\n\n"
    "    def bind_subject(\n",
)

# persistence authority persists invalidation.
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    "    def bind_subject(\n"
    "        self,\n"
    "        *,\n"
    "        task_id: str,\n"
    "        subject: VerificationSubject,\n"
    "        now: datetime | None = None,\n"
    "    ) -> TaskVerificationRequirement:\n"
    "        requirement = super().bind_subject(task_id=task_id, subject=subject, now=now)\n"
    "        self._save_requirements()\n"
    "        return requirement\n\n"
    "    def _save_requirements(self) -> None:\n",
    "    def bind_subject(\n"
    "        self,\n"
    "        *,\n"
    "        task_id: str,\n"
    "        subject: VerificationSubject,\n"
    "        now: datetime | None = None,\n"
    "    ) -> TaskVerificationRequirement:\n"
    "        requirement = super().bind_subject(task_id=task_id, subject=subject, now=now)\n"
    "        self._save_requirements()\n"
    "        return requirement\n\n"
    "    def invalidate_task_subject(\n"
    "        self,\n"
    "        task_id: str,\n"
    "        *,\n"
    "        now: datetime | None = None,\n"
    "    ) -> TaskVerificationRequirement | None:\n"
    "        requirement = super().invalidate_task_subject(task_id, now=now)\n"
    "        self._save_requirements()\n"
    "        return requirement\n\n"
    "    def _save_requirements(self) -> None:\n",
)

# package export for the optional kernel hook.
replace_once(
    "src/ai_multi_agent_platform/verification/__init__.py",
    "    CompletionGateDecision,\n"
    "    TaskVerificationRequirement,\n",
    "    CompletionGateDecision,\n"
    "    OutputChangeAwareCompletionAuthority,\n"
    "    TaskVerificationRequirement,\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/__init__.py",
    "    \"CompletionState\",\n"
    "    \"CanonicalVerificationRuntime\",\n",
    "    \"CompletionState\",\n"
    "    \"CanonicalVerificationRuntime\",\n"
    "    \"OutputChangeAwareCompletionAuthority\",\n",
)

# kernel.py: invalidate accepted/bound subjects whenever a new canonical output reference appears.
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    "from ai_multi_agent_platform.verification import CompletionAuthority, CompletionState\n",
    "from ai_multi_agent_platform.verification import (\n"
    "    CompletionAuthority,\n"
    "    CompletionState,\n"
    "    OutputChangeAwareCompletionAuthority,\n"
    ")\n",
)
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    "        await self._commit_task_command(\n"
    "            task=task,\n"
    "            key=idempotency_key,\n"
    "            operation=\"attach_artifact\",\n",
    "        await self._commit_task_command(\n"
    "            task=task,\n"
    "            key=idempotency_key,\n"
    "            operation=\"attach_artifact\",\n",
)
# Insert invalidation at the two return sites by operation-specific anchors.
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    "            source=source,\n"
    "        )\n"
    "        return await self.get_task(task_id)\n\n"
    "    async def attach_result(\n",
    "            source=source,\n"
    "        )\n"
    "        self._invalidate_completion_subject(task_id)\n"
    "        return await self.get_task(task_id)\n\n"
    "    async def attach_result(\n",
)
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    "            source=source,\n"
    "        )\n"
    "        return await self.get_task(task_id)\n\n"
    "    async def recover_task(\n",
    "            source=source,\n"
    "        )\n"
    "        self._invalidate_completion_subject(task_id)\n"
    "        return await self.get_task(task_id)\n\n"
    "    async def recover_task(\n",
)
# Add helper before recovery.
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    "    async def recover_task(self, task_id: str) -> RecoveryReport:\n",
    "    def _invalidate_completion_subject(self, task_id: str) -> None:\n"
    "        authority = self._completion_authority\n"
    "        if isinstance(authority, OutputChangeAwareCompletionAuthority):\n"
    "            authority.invalidate_task_subject(task_id)\n\n"
    "    async def recover_task(self, task_id: str) -> RecoveryReport:\n",
)

# evidence.py: one canonical submission path re-attests subject + evidence immediately before commit.
replace_once(
    "src/ai_multi_agent_platform/verification/evidence.py",
    "from .models import ProducerIdentity, VerificationRequest, VerificationSubject\n",
    "from .models import (\n"
    "    ProducerIdentity,\n"
    "    VerificationRequest,\n"
    "    VerificationResult,\n"
    "    VerificationSubject,\n"
    ")\n"
    "from .service import _CANONICAL_RESULT_TOKEN\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/evidence.py",
    "    async def request_reverification_after_repair(\n",
    "    async def submit_result(self, result: VerificationResult) -> VerificationResult:\n"
    "        request = self._completion.verification.get_request(result.verification_id)\n"
    "        canonical_subject = await self._evidence.resolve_subject(\n"
    "            task_id=request.task_id,\n"
    "            subject_type=request.subject.subject_type,\n"
    "            subject_id=request.subject.subject_id,\n"
    "        )\n"
    "        if canonical_subject != request.subject or result.subject != canonical_subject:\n"
    "            raise ContractError(\n"
    "                ErrorCode.CONTRACT_VIOLATION,\n"
    "                \"verification result subject differs from current canonical evidence\",\n"
    "            )\n"
    "        await self._evidence.validate_evidence_artifacts(\n"
    "            task_id=request.task_id,\n"
    "            artifact_ids=result.evidence_artifact_ids,\n"
    "        )\n"
    "        return self._completion.verification.submit_result(\n"
    "            result, _canonical_result_token=_CANONICAL_RESULT_TOKEN\n"
    "        )\n\n"
    "    async def request_reverification_after_repair(\n",
)

# control_plane.py: use runtime submission if provided; retain low-level compatibility otherwise.
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "from .evidence import VerificationEvidenceResolver\n",
    "from .evidence import CanonicalVerificationRuntime, VerificationEvidenceResolver\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "        verification: VerificationService,\n"
    "        evidence: VerificationEvidenceResolver | None = None,\n"
    "    ) -> None:\n"
    "        self._control_plane = control_plane\n"
    "        self._verification = verification\n"
    "        self._evidence = evidence\n",
    "        verification: VerificationService,\n"
    "        evidence: VerificationEvidenceResolver | None = None,\n"
    "        runtime: CanonicalVerificationRuntime | None = None,\n"
    "    ) -> None:\n"
    "        self._control_plane = control_plane\n"
    "        self._verification = verification\n"
    "        self._evidence = evidence\n"
    "        self._runtime = runtime\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "        result = self._verification.submit_result(\n"
    "            VerificationResult(\n",
    "        proposed = VerificationResult(\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "                },\n"
    "            )\n"
    "        )\n"
    "        return _verification_resource(self._verification.get_request(verification_id), result)\n",
    "                },\n"
    "            )\n"
    "        result = (\n"
    "            await self._runtime.submit_result(proposed)\n"
    "            if self._runtime is not None\n"
    "            else self._verification.submit_result(proposed)\n"
    "        )\n"
    "        return _verification_resource(self._verification.get_request(verification_id), result)\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "    evidence: VerificationEvidenceResolver | None = None,\n"
    ") -> None:\n",
    "    evidence: VerificationEvidenceResolver | None = None,\n"
    "    runtime: CanonicalVerificationRuntime | None = None,\n"
    ") -> None:\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    "    handlers = VerificationCommandHandlers(control_plane, verification, evidence)\n",
    "    handlers = VerificationCommandHandlers(\n"
    "        control_plane, verification, evidence, runtime\n"
    "    )\n",
)

# deployment: strict submission and canonical runtime for human review.
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    "    verification = SqliteVerificationService(verification_path, require_canonical_subjects=True)\n",
    "    verification = SqliteVerificationService(\n"
    "        verification_path,\n"
    "        require_canonical_subjects=True,\n"
    "        require_canonical_results=True,\n"
    "    )\n",
)
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    "        verification_evidence,\n"
    "    )\n",
    "        verification_evidence,\n"
    "        verification_runtime,\n"
    "    )\n",
)

# reviewer_agent.py: team-bound concrete reviewer + canonical re-attestation at completion.
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "from .evidence import VerificationEvidenceResolver\n",
    "from .evidence import CanonicalVerificationRuntime, VerificationEvidenceResolver\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "        evidence: VerificationEvidenceResolver | None = None,\n"
    "    ) -> None:\n"
    "        self._verification = verification\n"
    "        self._agents = agents\n"
    "        self._evidence = evidence\n",
    "        evidence: VerificationEvidenceResolver | None = None,\n"
    "        canonical_runtime: CanonicalVerificationRuntime | None = None,\n"
    "    ) -> None:\n"
    "        self._verification = verification\n"
    "        self._agents = agents\n"
    "        self._evidence = evidence\n"
    "        self._canonical_runtime = canonical_runtime\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "        agent_id: str,\n"
    "        revision: int | None = None,\n"
    "        mapper: AgentOrchestratorMapper | None = None,\n",
    "        agent_id: str,\n"
    "        revision: int | None = None,\n"
    "        team_id: str | None = None,\n"
    "        team_revision: int | None = None,\n"
    "        mapper: AgentOrchestratorMapper | None = None,\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "        policy = self._verification.get_policy(request.policy_id, request.policy_version)\n"
    "        stage = policy.stage(request.stage_id)\n"
    "        capability_ids = list(requested_capability_ids)\n",
    "        policy = self._verification.get_policy(request.policy_id, request.policy_version)\n"
    "        stage = policy.stage(request.stage_id)\n"
    "        bound_team = None\n"
    "        resolved_revision = revision\n"
    "        shared_capability_ids: tuple[str, ...] = ()\n"
    "        if team_id is None and team_revision is not None:\n"
    "            raise ContractError(\n"
    "                ErrorCode.INVALID_REQUEST,\n"
    "                \"reviewer team_revision requires team_id\",\n"
    "            )\n"
    "        if team_id is not None:\n"
    "            bound_team = self._agents.service.get_team_revision(team_id, team_revision)\n"
    "            member = next(\n"
    "                (\n"
    "                    item\n"
    "                    for item in bound_team.profile.members\n"
    "                    if item.agent.agent_id == agent_id\n"
    "                ),\n"
    "                None,\n"
    "            )\n"
    "            if member is None:\n"
    "                raise ContractError(\n"
    "                    ErrorCode.FORBIDDEN,\n"
    "                    \"reviewer Agent is not a member of the requested Team revision\",\n"
    "                )\n"
    "            if revision is not None and revision != member.agent.revision:\n"
    "                raise ContractError(\n"
    "                    ErrorCode.CONFLICT,\n"
    "                    \"reviewer Agent revision differs from the Team member revision\",\n"
    "                )\n"
    "            resolved_revision = member.agent.revision\n"
    "            shared_capability_ids = bound_team.profile.shared_capability_ids\n"
    "        capability_ids = list(requested_capability_ids)\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "            agent_id=agent_id,\n"
    "            revision=revision,\n"
    "            task_model_override=task_model_override,\n"
    "            requested_capability_ids=requested,\n",
    "            agent_id=agent_id,\n"
    "            revision=resolved_revision,\n"
    "            team_revision=bound_team,\n"
    "            task_model_override=task_model_override,\n"
    "            requested_capability_ids=requested,\n"
    "            shared_capability_ids=shared_capability_ids,\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "            agent_id=spec.agent_revision.agent_id,\n"
    "            revision=spec.agent_revision.revision,\n"
    "            mapper=mapper,\n"
    "            task_model_override=task_model_override,\n"
    "            requested_capability_ids=requested,\n",
    "            agent_id=spec.agent_revision.agent_id,\n"
    "            revision=spec.agent_revision.revision,\n"
    "            mapper=mapper,\n"
    "            team_revision=bound_team,\n"
    "            task_model_override=task_model_override,\n"
    "            requested_capability_ids=requested,\n"
    "            shared_capability_ids=shared_capability_ids,\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "    def complete_review(\n",
    "    async def complete_review(\n",
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    "        return self._verification.record_agent_review(\n"
    "            verification_id,\n"
    "            verifier=verifier,\n"
    "            outcome=outcome,\n"
    "            findings=findings,\n"
    "            evidence_artifact_ids=evidence_artifact_ids,\n"
    "            checks_executed=checks_executed,\n"
    "        )\n",
    "        proposed = VerificationResult(\n"
    "            verification_id=verification_id,\n"
    "            verifier=verifier,\n"
    "            outcome=outcome,\n"
    "            subject=request.subject,\n"
    "            findings=findings,\n"
    "            evidence_artifact_ids=evidence_artifact_ids,\n"
    "            checks_executed=checks_executed,\n"
    "        )\n"
    "        if self._canonical_runtime is not None:\n"
    "            return await self._canonical_runtime.submit_result(proposed)\n"
    "        return self._verification.submit_result(proposed)\n",
)

# Update existing synchronous reviewer tests/call sites to await complete_review.
for path in (
    "tests/test_issue_86_reviewer_agent.py",
    "tests/test_issue_86_replacement_conformance.py",
):
    target = ROOT / path
    text = target.read_text()
    text = text.replace("result = reviewer.complete_review(\n", "result = await reviewer.complete_review(\n")
    target.write_text(text)

# New focused regressions.
append_once(
    "tests/test_issue_86_authority_integrity.py",
    "test_output_attachment_automatically_invalidates_previously_accepted_subject",
    r'''

def test_output_attachment_automatically_invalidates_previously_accepted_subject() -> None:
    async def scenario() -> None:
        verification = VerificationService()
        completion = VerificationCompletionAuthority(verification)
        policy = verification.register_policy(
            VerificationPolicy(
                name="automatic-output-invalidation",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
            completion_authority=completion,
        )
        task = await kernel.create_task(
            idempotency_key="invalidate:create",
            title="Invalidate old review",
            objective="New output must require new verification",
            owner_type="user",
            owner_id="issue-86",
        )
        await kernel.ready_task(idempotency_key="invalidate:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="invalidate:start", task_id=task.task_id)
        lifecycle.complete(run.run_id, status=ExecutionStatus.SUCCEEDED, output={"v": 1})
        await kernel.refresh_run(
            idempotency_key="invalidate:refresh", task_id=task.task_id, run_id=run.run_id
        )
        first_result_id = new_id("result")
        await kernel.attach_result(
            idempotency_key="invalidate:first-result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=first_result_id,
        )
        first_subject = VerificationSubject(
            subject_type="result",
            subject_id=first_result_id,
            revision="1",
            digest="sha256:first",
        )
        request = completion.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=first_subject,
            result_id=first_result_id,
            correlation_id=task.task_id,
        )
        verification.record_human_review(
            request.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

        await kernel.attach_result(
            idempotency_key="invalidate:second-result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=new_id("result"),
        )
        requirement = completion.requirement_for(task.task_id)
        assert requirement is not None
        assert requirement.subject is None
        assert completion.assess_task_completion(task.task_id).state is CompletionState.WAITING
        blocked = await kernel.complete_task(
            idempotency_key="invalidate:complete", task_id=task.task_id
        )
        assert blocked.status is TaskStatus.WAITING

    asyncio.run(scenario())
''',
)
append_once(
    "tests/test_issue_86_hardening.py",
    "test_strict_canonical_result_submission_rechecks_current_subject",
    r'''

class _MutableEvidenceResolver:
    def __init__(self, subject: VerificationSubject) -> None:
        self.subject = subject
        self.validated: list[tuple[str, ...]] = []

    async def resolve_subject(
        self, *, task_id: str, subject_type: str, subject_id: str
    ) -> VerificationSubject:
        del task_id, subject_type, subject_id
        return self.subject

    async def resolve_context(
        self, *, task_id: str, subject_type: str, subject_id: str
    ) -> VerificationEvidenceContext:
        del subject_type, subject_id
        return VerificationEvidenceContext(
            task_id=task_id,
            subject=self.subject,
            run_id=None,
            project_id=None,
            capability_ids=(),
            producer=None,
        )

    async def validate_evidence_artifacts(
        self, *, task_id: str, artifact_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        del task_id
        self.validated.append(artifact_ids)
        return artifact_ids


def test_strict_canonical_result_submission_rechecks_current_subject() -> None:
    async def scenario() -> None:
        subject = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="1",
            digest="sha256:v1",
        )
        evidence = _MutableEvidenceResolver(subject)
        service = VerificationService(
            require_canonical_subjects=True,
            require_canonical_results=True,
        )
        completion = VerificationCompletionAuthority(service)
        runtime = CanonicalVerificationRuntime(completion, evidence)
        policy = service.register_policy(
            VerificationPolicy(
                name="strict-submission",
                stages=(VerificationStage("provider", VerifierKind.PROVIDER),),
            )
        )
        request = await runtime.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="provider",
            subject_type="result",
            subject_id=subject.subject_id,
            correlation_id="strict-result",
        )
        proposed = VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="provider:domain",
                kind=VerifierKind.PROVIDER,
                provider_id="domain",
            ),
            outcome=VerificationOutcome.PASS,
            subject=subject,
            checks_executed=("domain",),
        )
        with pytest.raises(ContractError) as raw:
            service.submit_result(proposed)
        assert raw.value.code is ErrorCode.FORBIDDEN

        evidence.subject = VerificationSubject(
            subject_type="result",
            subject_id=subject.subject_id,
            revision="2",
            digest="sha256:v2",
        )
        with pytest.raises(ContractError) as stale:
            await runtime.submit_result(proposed)
        assert stale.value.code is ErrorCode.CONTRACT_VIOLATION
        assert service.result_for(request.verification_id) is None

    asyncio.run(scenario())


def test_risk_class_can_selectively_forbid_self_verification() -> None:
    producer = ProducerIdentity(actor_ref="user:same")
    rules = ReviewerIndependence(
        forbid_self_verification_risk_classes=(RiskClassification.HIGH,)
    )
    same_human = VerifierIdentity(verifier_ref="user:same", kind=VerifierKind.HUMAN)

    high = VerificationService()
    high_policy = high.register_policy(
        VerificationPolicy(
            name="high-risk",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=rules,
            risk_classification=RiskClassification.HIGH,
        )
    )
    high_id, _high_subject, _high_task = _request(high, high_policy, producer=producer)
    with pytest.raises(ContractError) as denied:
        high.validate_verifier(high_id, same_human)
    assert denied.value.code is ErrorCode.FORBIDDEN

    standard = VerificationService()
    standard_policy = standard.register_policy(
        VerificationPolicy(
            name="standard-risk",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=rules,
            risk_classification=RiskClassification.STANDARD,
        )
    )
    standard_id, _subject_value, _task_id = _request(
        standard, standard_policy, producer=producer
    )
    assert standard.validate_verifier(standard_id, same_human) == same_human
''',
)

# Required imports for appended hardening tests.
replace_once(
    "tests/test_issue_86_hardening.py",
    "from ai_multi_agent_platform.verification import (\n",
    "from ai_multi_agent_platform.security.authorization import RiskClassification\n"
    "from ai_multi_agent_platform.verification import (\n",
)
# Only add names if they are not already present in the grouped import.
target = ROOT / "tests/test_issue_86_hardening.py"
text = target.read_text()
for name in ("CanonicalVerificationRuntime", "VerificationEvidenceContext", "VerificationResult", "VerifierIdentity"):
    if f"    {name},\n" not in text:
        text = text.replace("from ai_multi_agent_platform.verification import (\n", f"from ai_multi_agent_platform.verification import (\n    {name},\n", 1)
target.write_text(text)

# Team test uses existing reviewer test helpers.
replace_once(
    "tests/test_issue_86_reviewer_agent.py",
    "    AgentProfile,\n",
    "    AgentProfile,\n    AgentTeamMember,\n    AgentTeamProfile,\n    AgentRevisionRef,\n",
)
append_once(
    "tests/test_issue_86_reviewer_agent.py",
    "test_reviewer_agent_can_be_pinned_to_exact_team_revision",
    r'''

def test_reviewer_agent_can_be_pinned_to_exact_team_revision() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent = service.create_agent(
            _profile("Team reviewer"),
            owner_ref=OwnerRef(type="service", id="verification"),
        )
        team = service.create_team(
            AgentTeamProfile(
                name="Verification team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(agent_id=agent.agent_id, revision=1),
                        role="reviewer",
                    ),
                ),
            ),
            owner_ref=OwnerRef(type="service", id="verification"),
        )
        verification = VerificationService()
        policy = verification.register_policy(_policy())
        subject = _subject()
        request = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="team-review",
        )
        reviewer = ReviewerAgentRuntime(verification, AgentRuntime(service))
        record = await reviewer.start_review(
            request.verification_id,
            run_id=new_id("run"),
            agent_id=agent.agent_id,
            team_id=team.team_id,
            team_revision=team.revision,
        )
        assert record.team is not None
        assert record.team.team_id == team.team_id
        assert record.team.revision == team.revision
        result = await reviewer.complete_review(
            record.agent_run_id, outcome=VerificationOutcome.PASS
        )
        assert result.verifier.agent_id == agent.agent_id

    asyncio.run(scenario())
''',
)

# Docs.
append_once(
    "docs/VERIFICATION_HARDENING.md",
    "Final submission attestation",
    r'''
## Final submission attestation

The production-shaped Verification service now requires both canonical request subjects and canonical result submissions. `CanonicalVerificationRuntime.submit_result()` re-resolves the current Result/Artifact subject and validates every evidence Artifact immediately before the immutable `VerificationResult` is recorded. A review that started on revision V1 therefore cannot certify V1 after canonical output has advanced to V2, and direct raw `submit_result()` calls fail closed in strict mode.

Kernel `result.attached` and `artifact.attached` mutations notify completion authorities that opt into the output-change hook. Verification responds by clearing only the current Task→subject completion binding; historical requests/results remain immutable. A new exact subject must be canonically requested before Task completion can become accepted again.

`VerificationPolicy.risk_classification` uses the existing #15 `RiskClassification` vocabulary. `ReviewerIndependence.forbid_self_verification_risk_classes` can forbid self-verification only for selected policy risk classes while preserving lower-risk policy behavior.

Reviewer Teams are coordination context, not a new lifecycle authority. `ReviewerAgentRuntime.start_review()` can pin a concrete reviewer Agent to an exact Agent Team revision; membership and member revision are validated before execution, the resulting `AgentRunRecord.team` preserves Team provenance, and the canonical `VerificationResult` remains attributable to the concrete reviewer Agent.
''',
)
append_once(
    "docs/VERIFICATION.md",
    "Team reviewer semantics",
    r'''
## Team reviewer semantics

An Agent Team may coordinate a review, but canonical verifier identity remains a concrete Agent revision. The reviewer runtime can bind that Agent to an exact Team revision and preserves the Team reference on the AgentRun. This keeps N-reviewer independence and model/provider provenance attributable to actual reviewer executions instead of treating a Team as an opaque authority.
''',
)

print("#86 final attestation patch applied")
