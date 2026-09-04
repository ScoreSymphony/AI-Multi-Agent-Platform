from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_service() -> None:
    path = "src/ai_multi_agent_platform/verification/service.py"
    text = read(path)
    text = replace_once(
        text,
        ")\n\n\nclass VerificationService:",
        ")\n\n\n_CANONICAL_SUBJECT_TOKEN = object()\n\n\nclass VerificationService:",
        label="service token",
    )
    text = replace_once(
        text,
        "    def __init__(self) -> None:\n        self._policies: dict[tuple[str, int], VerificationPolicy] = {}",
        "    def __init__(self, *, require_canonical_subjects: bool = False) -> None:\n        self._require_canonical_subjects = require_canonical_subjects\n        self._policies: dict[tuple[str, int], VerificationPolicy] = {}",
        label="service init",
    )
    text = replace_once(
        text,
        "        causation_id: str | None = None,\n        now: datetime | None = None,\n    ) -> VerificationRequest:\n        policy = self.get_policy(policy_id, policy_version)",
        "        causation_id: str | None = None,\n        now: datetime | None = None,\n        _canonical_subject_token: object | None = None,\n    ) -> VerificationRequest:\n        if (\n            self._require_canonical_subjects\n            and _canonical_subject_token is not _CANONICAL_SUBJECT_TOKEN\n        ):\n            raise ContractError(\n                ErrorCode.FORBIDDEN,\n                \"raw verification subjects are disabled; use CanonicalVerificationRuntime\",\n            )\n        policy = self.get_policy(policy_id, policy_version)",
        label="service strict request",
    )
    text = replace_once(
        text,
        "        artifact_ids: tuple[str, ...] = (),\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        previous = self.get_request(verification_id)",
        "        artifact_ids: tuple[str, ...] = (),\n        causation_id: str | None = None,\n        producer: ProducerIdentity | None = None,\n        project_id: str | None = None,\n        capability_ids: tuple[str, ...] | None = None,\n        _replace_context: bool = False,\n        _canonical_subject_token: object | None = None,\n    ) -> VerificationRequest:\n        if (\n            self._require_canonical_subjects\n            and _canonical_subject_token is not _CANONICAL_SUBJECT_TOKEN\n        ):\n            raise ContractError(\n                ErrorCode.FORBIDDEN,\n                \"raw repaired verification subjects are disabled; use CanonicalVerificationRuntime\",\n            )\n        previous = self.get_request(verification_id)",
        label="service strict reverify",
    )
    text = replace_once(
        text,
        "            project_id=previous.project_id,\n            capability_ids=previous.capability_ids,\n            producer=previous.producer,\n            repair_attempt=next_attempt,\n            causation_id=causation_id,\n        )",
        "            project_id=project_id if _replace_context else previous.project_id,\n            capability_ids=(\n                capability_ids\n                if _replace_context and capability_ids is not None\n                else previous.capability_ids\n            ),\n            producer=producer if _replace_context else previous.producer,\n            repair_attempt=next_attempt,\n            causation_id=causation_id,\n            _canonical_subject_token=_canonical_subject_token,\n        )",
        label="service replacement producer context",
    )
    prepass_anchor = "        blocking: list[str] = []\n        max_repair_attempt = 0\n"
    prepass = """        # Definitive outcomes must be evaluated across every stage before an\n        # earlier incomplete stage can return WAITING. This makes the policy invariant\n        # \"any critical failure blocks completion\" independent of stage ordering.\n        stage_matches = {\n            stage.stage_id: self._matching_stage_results(\n                task_id=task_id,\n                subject=subject,\n                policy=policy,\n                stage_id=stage.stage_id,\n                now=current,\n            )\n            for stage in policy.stages\n        }\n        observed_repair_attempt = max(\n            (\n                request.repair_attempt\n                for matching in stage_matches.values()\n                for request, _result in matching\n            ),\n            default=0,\n        )\n        for stage in policy.stages:\n            matching = stage_matches[stage.stage_id]\n            if stage.critical and any(\n                result.outcome is VerificationOutcome.FAIL for _request, result in matching\n            ):\n                return CompletionAssessment(\n                    task_id=task_id,\n                    subject=subject,\n                    state=self._failure_state(policy),\n                    reason=\"critical verification stage failed\",\n                    policy_id=policy_id,\n                    policy_version=policy_version,\n                    blocking_verification_ids=tuple(\n                        request.verification_id\n                        for request, result in matching\n                        if result.outcome is VerificationOutcome.FAIL\n                    ),\n                    repair_attempts_remaining=max(\n                        0, policy.max_repair_attempts - observed_repair_attempt\n                    ),\n                )\n        for stage in policy.stages:\n            matching = stage_matches[stage.stage_id]\n            if any(\n                result.outcome is VerificationOutcome.NEEDS_CHANGES\n                for _request, result in matching\n            ):\n                remaining = max(\n                    0, policy.max_repair_attempts - observed_repair_attempt\n                )\n                state = (\n                    CompletionState.REPAIR_REQUIRED\n                    if remaining > 0\n                    else self._failure_state(policy)\n                )\n                return CompletionAssessment(\n                    task_id=task_id,\n                    subject=subject,\n                    state=state,\n                    reason=(\n                        \"verification requested changes\"\n                        if remaining > 0\n                        else \"verification repair limit exhausted\"\n                    ),\n                    policy_id=policy_id,\n                    policy_version=policy_version,\n                    blocking_verification_ids=tuple(\n                        request.verification_id\n                        for request, result in matching\n                        if result.outcome is VerificationOutcome.NEEDS_CHANGES\n                    ),\n                    repair_attempts_remaining=remaining,\n                )\n\n        blocking: list[str] = []\n        max_repair_attempt = 0\n"""
    text = replace_once(text, prepass_anchor, prepass, label="service stage prepass")
    text = replace_once(
        text,
        "                distinct = {result.verifier.verifier_ref for _request, result in accepted}\n                accepted_count = len(distinct)",
        "                distinct = {\n                    self._distinct_verifier_key(result.verifier)\n                    for _request, result in accepted\n                }\n                accepted_count = len(distinct)",
        label="service distinct reviewer key",
    )
    helper_anchor = "    @staticmethod\n    def _failure_state_for(\n"
    helper = """    @staticmethod\n    def _distinct_verifier_key(verifier: VerifierIdentity) -> str:\n        if verifier.kind is VerifierKind.AGENT:\n            assert verifier.agent_id is not None\n            return f\"agent:{verifier.agent_id}\"\n        if verifier.kind is VerifierKind.HUMAN:\n            return f\"human:{verifier.verifier_ref}\"\n        if verifier.kind is VerifierKind.PROVIDER:\n            provider = verifier.provider_id or \"unknown-provider\"\n            return f\"provider:{provider}:{verifier.verifier_ref}\"\n        return f\"deterministic:{verifier.verifier_ref}\"\n\n    @staticmethod\n    def _failure_state_for(\n"""
    text = replace_once(text, helper_anchor, helper, label="service distinct helper")
    write(path, text)


def patch_persistence() -> None:
    path = "src/ai_multi_agent_platform/verification/persistence.py"
    text = read(path)
    text = replace_once(
        text,
        "    def __init__(self, db_path: str | Path) -> None:\n        VerificationService.__init__(self)",
        "    def __init__(\n        self,\n        db_path: str | Path,\n        *,\n        require_canonical_subjects: bool = False,\n    ) -> None:\n        VerificationService.__init__(\n            self, require_canonical_subjects=require_canonical_subjects\n        )",
        label="sqlite strict init",
    )
    text = replace_once(
        text,
        "        causation_id: str | None = None,\n        now: datetime | None = None,\n    ) -> VerificationRequest:\n        request = super().request_verification(",
        "        causation_id: str | None = None,\n        now: datetime | None = None,\n        _canonical_subject_token: object | None = None,\n    ) -> VerificationRequest:\n        request = super().request_verification(",
        label="sqlite request token signature",
    )
    text = replace_once(
        text,
        "            causation_id=causation_id,\n            now=now,\n        )\n        self._save_service_state()",
        "            causation_id=causation_id,\n            now=now,\n            _canonical_subject_token=_canonical_subject_token,\n        )\n        self._save_service_state()",
        label="sqlite request token forward",
    )
    write(path, text)


def patch_gate() -> None:
    path = "src/ai_multi_agent_platform/verification/gate.py"
    text = read(path)
    text = replace_once(
        text,
        "from .service import VerificationService",
        "from .service import VerificationService, _CANONICAL_SUBJECT_TOKEN",
        label="gate token import",
    )
    text = replace_once(
        text,
        "    def __init__(self, verification: VerificationService) -> None:\n        self._verification = verification\n        self._requirements: dict[str, TaskVerificationRequirement] = {}\n",
        "    def __init__(self, verification: VerificationService) -> None:\n        self._verification = verification\n        self._requirements: dict[str, TaskVerificationRequirement] = {}\n\n    @property\n    def verification(self) -> VerificationService:\n        return self._verification\n",
        label="gate verification property",
    )
    canonical_request = '''    def request_canonical_verification(\n        self,\n        *,\n        task_id: str,\n        policy_id: str,\n        policy_version: int,\n        stage_id: str,\n        subject: VerificationSubject,\n        correlation_id: str,\n        run_id: str | None = None,\n        result_id: str | None = None,\n        artifact_ids: tuple[str, ...] = (),\n        project_id: str | None = None,\n        capability_ids: tuple[str, ...] = (),\n        producer: ProducerIdentity | None = None,\n        repair_attempt: int = 0,\n        causation_id: str | None = None,\n        now: datetime | None = None,\n    ) -> VerificationRequest:\n        \"\"\"Bind a subject that was resolved from canonical platform evidence.\"\"\"\n\n        request = self._verification.request_verification(\n            task_id=task_id,\n            policy_id=policy_id,\n            policy_version=policy_version,\n            stage_id=stage_id,\n            subject=subject,\n            correlation_id=correlation_id,\n            run_id=run_id,\n            result_id=result_id,\n            artifact_ids=artifact_ids,\n            project_id=project_id,\n            capability_ids=capability_ids,\n            producer=producer,\n            repair_attempt=repair_attempt,\n            causation_id=causation_id,\n            now=now,\n            _canonical_subject_token=_CANONICAL_SUBJECT_TOKEN,\n        )\n        self.require_task(\n            task_id=task_id,\n            policy_id=policy_id,\n            policy_version=policy_version,\n            now=now,\n        )\n        self.bind_subject(task_id=task_id, subject=subject, now=now)\n        return request\n\n'''
    text = replace_once(
        text,
        "    def request_reverification_after_repair(\n",
        canonical_request + "    def request_reverification_after_repair(\n",
        label="gate canonical request method",
    )
    canonical_repair = '''    def request_canonical_reverification_after_repair(\n        self,\n        verification_id: str,\n        *,\n        new_subject: VerificationSubject,\n        correlation_id: str,\n        run_id: str | None = None,\n        result_id: str | None = None,\n        artifact_ids: tuple[str, ...] = (),\n        project_id: str | None = None,\n        capability_ids: tuple[str, ...] = (),\n        producer: ProducerIdentity | None = None,\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        \"\"\"Rebind a repaired subject and its newly derived producer context.\"\"\"\n\n        request = self._verification.request_reverification_after_repair(\n            verification_id,\n            new_subject=new_subject,\n            correlation_id=correlation_id,\n            run_id=run_id,\n            result_id=result_id,\n            artifact_ids=artifact_ids,\n            project_id=project_id,\n            capability_ids=capability_ids,\n            producer=producer,\n            causation_id=causation_id,\n            _replace_context=True,\n            _canonical_subject_token=_CANONICAL_SUBJECT_TOKEN,\n        )\n        self.require_task(\n            task_id=request.task_id,\n            policy_id=request.policy_id,\n            policy_version=request.policy_version,\n        )\n        self.bind_subject(task_id=request.task_id, subject=request.subject)\n        return request\n\n'''
    text = replace_once(
        text,
        "    def assess_task_completion(self, task_id: str) -> CompletionGateDecision:\n",
        canonical_repair + "    def assess_task_completion(self, task_id: str) -> CompletionGateDecision:\n",
        label="gate canonical reverify method",
    )
    write(path, text)


def patch_evidence() -> None:
    path = "src/ai_multi_agent_platform/verification/evidence.py"
    text = read(path)
    text = replace_once(
        text,
        "from collections.abc import Mapping\nfrom enum import Enum",
        "from collections.abc import Mapping\nfrom dataclasses import dataclass\nfrom enum import Enum",
        label="evidence dataclass import",
    )
    text = replace_once(
        text,
        "if TYPE_CHECKING:\n    from ai_multi_agent_platform.kernel import PlatformKernel\n    from ai_multi_agent_platform.kernel.repository import EventRepository\n",
        "if TYPE_CHECKING:\n    from ai_multi_agent_platform.agents import AgentRepository\n    from ai_multi_agent_platform.kernel import PlatformKernel\n    from ai_multi_agent_platform.kernel.repository import EventRepository\n",
        label="evidence agent repo type",
    )
    context_model = '''\n\n@dataclass(frozen=True, slots=True)\nclass VerificationEvidenceContext:\n    \"\"\"Canonical subject plus producer/scope facts derived from platform state.\"\"\"\n\n    task_id: str\n    subject: VerificationSubject\n    run_id: str | None\n    project_id: str | None\n    capability_ids: tuple[str, ...]\n    producer: ProducerIdentity | None\n\n'''
    text = replace_once(
        text,
        "@runtime_checkable\nclass VerificationEvidenceResolver(Protocol):",
        context_model + "@runtime_checkable\nclass VerificationEvidenceResolver(Protocol):",
        label="evidence context model",
    )
    protocol_anchor = "    async def validate_evidence_artifacts(\n"
    protocol_method = '''    async def resolve_context(\n        self,\n        *,\n        task_id: str,\n        subject_type: str,\n        subject_id: str,\n    ) -> VerificationEvidenceContext: ...\n\n'''
    text = replace_once(
        text,
        protocol_anchor,
        protocol_method + protocol_anchor,
        label="evidence context protocol",
    )
    text = replace_once(
        text,
        "        files: FileProvider,\n    ) -> None:\n        self._kernel = kernel\n        self._events = events\n        self._files = files\n",
        "        files: FileProvider,\n        agents: AgentRepository | None = None,\n    ) -> None:\n        self._kernel = kernel\n        self._events = events\n        self._files = files\n        self._agents = agents\n",
        label="evidence resolver agents",
    )
    context_method = '''    async def resolve_context(\n        self,\n        *,\n        task_id: str,\n        subject_type: str,\n        subject_id: str,\n    ) -> VerificationEvidenceContext:\n        task = await self._kernel.get_task(task_id)\n        subject = await self.resolve_subject(\n            task_id=task_id,\n            subject_type=subject_type,\n            subject_id=subject_id,\n        )\n        run_id: str | None = None\n        if subject_type == \"result\":\n            attachments = [\n                event\n                for event in await self._events.read_events(task_id)\n                if event.event_type == \"result.attached\"\n                and event.payload.get(\"result_id\") == subject_id\n            ]\n            if not attachments or attachments[-1].subject_type != \"run\":\n                raise ContractError(\n                    ErrorCode.CONTRACT_VIOLATION,\n                    \"canonical Result producer context requires a Run attachment\",\n                )\n            run_id = attachments[-1].subject_id\n        producer, capability_ids, producer_run_id = self._producer_context(\n            task_id=task_id,\n            subject_type=subject_type,\n            subject_id=subject_id,\n            run_id=run_id,\n        )\n        if run_id is None:\n            run_id = producer_run_id\n        elif producer_run_id is not None and producer_run_id != run_id:\n            raise ContractError(\n                ErrorCode.CONTRACT_VIOLATION,\n                \"canonical producer AgentRun belongs to a different Run\",\n            )\n        return VerificationEvidenceContext(\n            task_id=task_id,\n            subject=subject,\n            run_id=run_id,\n            project_id=task.task.project_id,\n            capability_ids=capability_ids,\n            producer=producer,\n        )\n\n    def _producer_context(\n        self,\n        *,\n        task_id: str,\n        subject_type: str,\n        subject_id: str,\n        run_id: str | None,\n    ) -> tuple[ProducerIdentity | None, tuple[str, ...], str | None]:\n        if self._agents is None:\n            return None, (), run_id\n        records = self._agents.list_agent_runs(run_id) if run_id is not None else self._agents.list_agent_runs()\n        candidates = [\n            record\n            for record in records\n            if record.task_id == task_id\n            and (\n                subject_id in record.result_ids\n                if subject_type == \"result\"\n                else subject_id in record.artifact_ids\n            )\n        ]\n        if len(candidates) > 1:\n            raise ContractError(\n                ErrorCode.CONTRACT_VIOLATION,\n                \"verification subject maps to multiple canonical producer AgentRuns\",\n            )\n        if not candidates:\n            return None, (), run_id\n        record = candidates[0]\n        return (\n            ProducerIdentity(\n                actor_ref=f\"agent:{record.agent.agent_id}@{record.agent.revision}\",\n                agent_id=record.agent.agent_id,\n                agent_revision=record.agent.revision,\n                model_config_id=record.selected_model_config_id,\n                provider_id=record.selected_provider_id,\n            ),\n            record.capability_ids,\n            record.run_id,\n        )\n\n'''
    text = replace_once(
        text,
        "    async def validate_evidence_artifacts(\n",
        context_method + "    async def validate_evidence_artifacts(\n",
        label="evidence context implementation",
    )
    runtime_pattern = re.compile(
        r"    async def request_verification\(.*?\n\n    async def request_reverification_after_repair\(.*?\n\n\ndef _file_context",
        re.S,
    )
    replacement = '''    def require_task(\n        self,\n        *,\n        task_id: str,\n        policy_id: str,\n        policy_version: int,\n    ):\n        return self._completion.require_task(\n            task_id=task_id,\n            policy_id=policy_id,\n            policy_version=policy_version,\n        )\n\n    async def request_verification(\n        self,\n        *,\n        task_id: str,\n        policy_id: str,\n        policy_version: int,\n        stage_id: str,\n        subject_type: str,\n        subject_id: str,\n        correlation_id: str,\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        context = await self._evidence.resolve_context(\n            task_id=task_id,\n            subject_type=subject_type,\n            subject_id=subject_id,\n        )\n        result_id = subject_id if subject_type == \"result\" else None\n        artifact_ids = (subject_id,) if subject_type == \"artifact\" else ()\n        return self._completion.request_canonical_verification(\n            task_id=context.task_id,\n            policy_id=policy_id,\n            policy_version=policy_version,\n            stage_id=stage_id,\n            subject=context.subject,\n            correlation_id=correlation_id,\n            run_id=context.run_id,\n            result_id=result_id,\n            artifact_ids=artifact_ids,\n            project_id=context.project_id,\n            capability_ids=context.capability_ids,\n            producer=context.producer,\n            causation_id=causation_id,\n        )\n\n    async def request_reverification_after_repair(\n        self,\n        verification_id: str,\n        *,\n        subject_type: str,\n        subject_id: str,\n        correlation_id: str,\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        previous = self._completion.verification.get_request(verification_id)\n        context = await self._evidence.resolve_context(\n            task_id=previous.task_id,\n            subject_type=subject_type,\n            subject_id=subject_id,\n        )\n        result_id = subject_id if subject_type == \"result\" else None\n        artifact_ids = (subject_id,) if subject_type == \"artifact\" else ()\n        return self._completion.request_canonical_reverification_after_repair(\n            verification_id,\n            new_subject=context.subject,\n            correlation_id=correlation_id,\n            run_id=context.run_id,\n            result_id=result_id,\n            artifact_ids=artifact_ids,\n            project_id=context.project_id,\n            capability_ids=context.capability_ids,\n            producer=context.producer,\n            causation_id=causation_id,\n        )\n\n\ndef _file_context'''
    text, count = runtime_pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"evidence runtime rewrite: expected 1, found {count}")
    write(path, text)


def patch_exports() -> None:
    path = "src/ai_multi_agent_platform/verification/__init__.py"
    text = read(path)
    text = replace_once(
        text,
        "    VerificationEvidenceResolver,\n)",
        "    VerificationEvidenceContext,\n    VerificationEvidenceResolver,\n)",
        label="verification evidence context import",
    )
    text = replace_once(
        text,
        '    "VerificationEvidenceResolver",\n',
        '    "VerificationEvidenceContext",\n    "VerificationEvidenceResolver",\n',
        label="verification evidence context export",
    )
    write(path, text)


def patch_repair() -> None:
    path = "src/ai_multi_agent_platform/verification/repair.py"
    text = read(path)
    text = replace_once(
        text,
        '        key = f"verification-repair:{verification_id}:{repair_attempt}:{idempotency_key}"\n',
        '        key = f"verification-repair:{verification_id}:{repair_attempt}"\n        existing = await self._existing_execution(\n            request.task_id, verification_id, repair_attempt, key\n        )\n        if existing is not None:\n            return existing\n',
        label="repair stable round key",
    )
    helper_anchor = "        return VerificationRepairExecution(\n            source_verification_id=verification_id,\n            task_id=request.task_id,\n            plan_id=planned.plan_ref,\n            step_id=selected_step,\n            run_id=started.run_id,\n            repair_attempt=repair_attempt,\n        )\n"
    helper = helper_anchor + '''\n    async def _existing_execution(\n        self,\n        task_id: str,\n        verification_id: str,\n        repair_attempt: int,\n        key: str,\n    ) -> VerificationRepairExecution | None:\n        matches = [\n            event\n            for event in await self._kernel.history(task_id)\n            if event.event_type == \"run.created\"\n            and event.provenance is not None\n            and event.provenance.source == _REPAIR_SOURCE\n            and event.causation_id == f\"{key}:create-run\"\n        ]\n        if len(matches) > 1:\n            raise ContractError(\n                ErrorCode.CONTRACT_VIOLATION,\n                \"one verification repair round has multiple canonical repair runs\",\n            )\n        if not matches:\n            return None\n        event = matches[0]\n        plan_id = event.payload.get(\"plan_ref\")\n        step_id = event.payload.get(\"subject_id\")\n        if not isinstance(plan_id, str) or not isinstance(step_id, str):\n            raise ContractError(\n                ErrorCode.CONTRACT_VIOLATION,\n                \"canonical repair run is missing plan/step provenance\",\n            )\n        return VerificationRepairExecution(\n            source_verification_id=verification_id,\n            task_id=task_id,\n            plan_id=plan_id,\n            step_id=step_id,\n            run_id=event.subject_id,\n            repair_attempt=repair_attempt,\n        )\n'''
    text = replace_once(text, helper_anchor, helper, label="repair existing execution helper")
    write(path, text)


def patch_deployment() -> None:
    path = "src/ai_multi_agent_platform/deployment/single_node.py"
    text = read(path)
    text = replace_once(
        text,
        "    verification_completion: SqliteVerificationCompletionAuthority\n",
        "",
        label="deployment hide raw completion authority",
    )
    text = replace_once(
        text,
        "    verification = SqliteVerificationService(verification_path)\n",
        "    verification = SqliteVerificationService(\n        verification_path, require_canonical_subjects=True\n    )\n",
        label="deployment strict verification service",
    )
    text = replace_once(
        text,
        "    verification_evidence = KernelFileVerificationEvidenceResolver(kernel, kernel_repository, files)\n",
        "    verification_evidence = KernelFileVerificationEvidenceResolver(\n        kernel, kernel_repository, files, agents.repository\n    )\n",
        label="deployment producer resolver",
    )
    text = replace_once(
        text,
        "        verification=verification,\n        verification_completion=verification_completion,\n        verification_runtime=verification_runtime,\n",
        "        verification=verification,\n        verification_runtime=verification_runtime,\n",
        label="deployment return hide completion",
    )
    write(path, text)


def patch_existing_tests() -> None:
    path = "tests/test_issue_86_hardening.py"
    text = read(path)
    text = text.replace("deployment.verification_completion.require_task(", "deployment.verification_runtime.require_task(")
    text = text.replace(
        "            correlation_id=task.task_id,\n            run_id=run.run_id,\n        )",
        "            correlation_id=task.task_id,\n        )",
    )
    text = text.replace(
        "            restarted.verification_completion.assess_task_completion(task.task_id).state\n            is CompletionState.ACCEPTED\n",
        "            restarted.kernel._completion_authority is not None\n            and restarted.kernel._completion_authority.assess_task_completion(task.task_id).state\n            is CompletionState.ACCEPTED\n",
    )
    write(path, text)

    path = "tests/test_issue_86_hardening_integration.py"
    text = read(path)
    forged_pattern = re.compile(
        r"        forged = VerificationSubject\(.*?        assert deployment\.verification\.result_for\(forged_request\.verification_id\) is None\n\n",
        re.S,
    )
    forged_replacement = '''        forged = VerificationSubject(\n            subject_type=\"result\",\n            subject_id=result_id,\n            revision=canonical.revision,\n            digest=\"sha256:forged\",\n        )\n        with pytest.raises(ContractError) as forged_error:\n            deployment.verification.request_verification(\n                task_id=task.task_id,\n                policy_id=policy.policy_id,\n                policy_version=policy.version,\n                stage_id=\"review\",\n                subject=forged,\n                correlation_id=task.task_id,\n                run_id=run.run_id,\n                result_id=result_id,\n            )\n        assert forged_error.value.code is ErrorCode.FORBIDDEN\n\n'''
    text, count = forged_pattern.subn(forged_replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"hardening integration forged rewrite: expected 1, found {count}")
    text = text.replace(
        "            correlation_id=task.task_id,\n            run_id=run.run_id,\n        )",
        "            correlation_id=task.task_id,\n        )",
    )
    write(path, text)


def append_regression_tests() -> None:
    path = "tests/test_issue_86_verification.py"
    text = read(path)
    additions = r'''\n\ndef test_later_critical_stage_failure_overrides_earlier_incomplete_stage() -> None:\n    service = VerificationService()\n    policy = service.register_policy(\n        VerificationPolicy(\n            name=\"multi-stage critical ordering\",\n            stages=(\n                VerificationStage(\"human\", VerifierKind.HUMAN),\n                VerificationStage(\"tests\", VerifierKind.DETERMINISTIC, critical=True),\n            ),\n        )\n    )\n    task_id = new_id(\"task\")\n    exact = subject()\n    service.request_verification(\n        task_id=task_id,\n        policy_id=policy.policy_id,\n        policy_version=policy.version,\n        stage_id=\"human\",\n        subject=exact,\n        result_id=exact.subject_id,\n        correlation_id=\"human-pending\",\n    )\n    failed = service.request_verification(\n        task_id=task_id,\n        policy_id=policy.policy_id,\n        policy_version=policy.version,\n        stage_id=\"tests\",\n        subject=exact,\n        result_id=exact.subject_id,\n        correlation_id=\"tests-fail\",\n    )\n    service.run_deterministic(\n        failed.verification_id,\n        ReferenceDeterministicVerifier(\n            \"deterministic:critical\",\n            (DeterministicCheck(\"tests\", lambda _request: False, \"critical tests failed\"),),\n        ),\n    )\n    decision = service.assess_completion(\n        task_id=task_id,\n        subject=exact,\n        policy_id=policy.policy_id,\n        policy_version=policy.version,\n    )\n    assert decision.state is CompletionState.REJECTED\n    assert decision.blocking_verification_ids == (failed.verification_id,)\n\n\ndef test_agent_revisions_do_not_count_as_distinct_reviewers() -> None:\n    service = VerificationService()\n    policy = service.register_policy(\n        VerificationPolicy(\n            name=\"two independent agent reviewers\",\n            stages=(VerificationStage(\"review\", VerifierKind.AGENT, minimum_results=2),),\n            independence=ReviewerIndependence(require_distinct_verifiers=True),\n        )\n    )\n    task_id = new_id(\"task\")\n    exact = subject()\n    same_agent = new_id(\"agent\")\n\n    def request(correlation: str) -> str:\n        return service.request_verification(\n            task_id=task_id,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n            stage_id=\"review\",\n            subject=exact,\n            result_id=exact.subject_id,\n            correlation_id=correlation,\n        ).verification_id\n\n    for revision in (1, 2):\n        service.record_agent_review(\n            request(f\"same-agent-{revision}\"),\n            verifier=VerifierIdentity(\n                verifier_ref=f\"agent:{same_agent}@{revision}\",\n                kind=VerifierKind.AGENT,\n                agent_id=same_agent,\n                agent_revision=revision,\n                read_only=True,\n            ),\n            outcome=VerificationOutcome.PASS,\n        )\n    waiting = service.assess_completion(\n        task_id=task_id,\n        subject=exact,\n        policy_id=policy.policy_id,\n        policy_version=policy.version,\n    )\n    assert waiting.state is CompletionState.WAITING\n\n    other_agent = new_id(\"agent\")\n    service.record_agent_review(\n        request(\"other-agent\"),\n        verifier=VerifierIdentity(\n            verifier_ref=f\"agent:{other_agent}@1\",\n            kind=VerifierKind.AGENT,\n            agent_id=other_agent,\n            agent_revision=1,\n            read_only=True,\n        ),\n        outcome=VerificationOutcome.PASS,\n    )\n    assert (\n        service.assess_completion(\n            task_id=task_id,\n            subject=exact,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n        ).state\n        is CompletionState.ACCEPTED\n    )\n'''
    if "test_later_critical_stage_failure_overrides_earlier_incomplete_stage" not in text:
        text += additions.replace("\\n", "\n")
    write(path, text)

    path = "tests/test_issue_86_repair_runtime.py"
    text = read(path)
    addition = r'''\n\ndef test_repair_round_reuses_same_execution_even_with_new_caller_key() -> None:\n    async def scenario() -> None:\n        (\n            verification,\n            completion,\n            kernel,\n            lifecycle,\n            task_id,\n            verification_id,\n            _plan,\n        ) = await _needs_changes_stack()\n        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)\n        first = await repair_runtime.start_repair(\n            verification_id,\n            idempotency_key=\"first-caller-key\",\n        )\n        lifecycle.complete(\n            first.run_id,\n            status=ExecutionStatus.SUCCEEDED,\n            output={\"answer\": \"repair finished\"},\n        )\n        await kernel.refresh_run(\n            idempotency_key=\"first-caller-key:refresh\",\n            task_id=task_id,\n            run_id=first.run_id,\n        )\n        second = await repair_runtime.start_repair(\n            verification_id,\n            idempotency_key=\"different-caller-key\",\n        )\n        assert second == first\n        repair_runs = [\n            event\n            for event in await kernel.history(task_id)\n            if event.event_type == \"run.created\"\n            and event.provenance is not None\n            and event.provenance.source == \"verification-repair\"\n        ]\n        assert len(repair_runs) == 1\n\n    asyncio.run(scenario())\n'''
    if "test_repair_round_reuses_same_execution_even_with_new_caller_key" not in text:
        text += addition.replace("\\n", "\n")
    write(path, text)


def create_authority_integrity_tests() -> None:
    path = ROOT / "tests/test_issue_86_authority_integrity.py"
    path.write_text('''from __future__ import annotations\n\nimport asyncio\nfrom dataclasses import replace\n\nimport pytest\n\nfrom ai_multi_agent_platform.agents import (\n    AgentRevisionRef,\n    AgentRunRecord,\n    AgentRunStatus,\n    InMemoryAgentRepository,\n    new_agent_run_id,\n)\nfrom ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus\nfrom ai_multi_agent_platform.data import LocalFileProvider\nfrom ai_multi_agent_platform.domain import new_id\nfrom ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel\nfrom ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator\nfrom ai_multi_agent_platform.verification import (\n    CanonicalVerificationRuntime,\n    KernelFileVerificationEvidenceResolver,\n    ReviewerIndependence,\n    VerificationCompletionAuthority,\n    VerificationOutcome,\n    VerificationPolicy,\n    VerificationService,\n    VerificationStage,\n    VerificationSubject,\n    VerifierKind,\n)\n\n\ndef test_strict_canonical_mode_rejects_raw_subject_requests(tmp_path) -> None:\n    service = VerificationService(require_canonical_subjects=True)\n    completion = VerificationCompletionAuthority(service)\n    policy = service.register_policy(\n        VerificationPolicy(\n            name=\"strict canonical\",\n            stages=(VerificationStage(\"review\", VerifierKind.HUMAN),),\n        )\n    )\n    task_id = new_id(\"task\")\n    result_id = new_id(\"result\")\n    forged = VerificationSubject(\n        subject_type=\"result\",\n        subject_id=result_id,\n        revision=\"forged\",\n        digest=\"sha256:forged\",\n    )\n    with pytest.raises(ContractError) as error:\n        completion.request_verification(\n            task_id=task_id,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n            stage_id=\"review\",\n            subject=forged,\n            correlation_id=task_id,\n            result_id=result_id,\n        )\n    assert error.value.code is ErrorCode.FORBIDDEN\n\n\ndef test_canonical_runtime_derives_producer_project_and_capabilities(tmp_path) -> None:\n    async def scenario() -> None:\n        repository = InMemoryKernelRepository()\n        lifecycle = FakeLifecycleBackend()\n        kernel = PlatformKernel(\n            orchestrator=FakeOrchestrator(),\n            lifecycle=lifecycle,\n            repository=repository,\n        )\n        project_id = new_id(\"project\")\n        task = await kernel.create_task(\n            idempotency_key=\"producer:create\",\n            title=\"Canonical producer\",\n            objective=\"Derive producer provenance\",\n            owner_type=\"user\",\n            owner_id=\"issue-86\",\n            project_id=project_id,\n        )\n        await kernel.ready_task(idempotency_key=\"producer:ready\", task_id=task.task_id)\n        run = await kernel.start_task(idempotency_key=\"producer:start\", task_id=task.task_id)\n        lifecycle.complete(\n            run.run_id, status=ExecutionStatus.SUCCEEDED, output={\"answer\": 86}\n        )\n        await kernel.refresh_run(\n            idempotency_key=\"producer:refresh\", task_id=task.task_id, run_id=run.run_id\n        )\n        result_id = new_id(\"result\")\n        await kernel.attach_result(\n            idempotency_key=\"producer:result\",\n            task_id=task.task_id,\n            run_id=run.run_id,\n            result_id=result_id,\n        )\n        agent_id = new_id(\"agent\")\n        capability_id = new_id(\"cap\")\n        agents = InMemoryAgentRepository()\n        agents.create_agent_run(\n            AgentRunRecord(\n                agent_run_id=new_agent_run_id(),\n                run_id=run.run_id,\n                task_id=task.task_id,\n                agent=AgentRevisionRef(agent_id=agent_id, revision=3),\n                status=AgentRunStatus.SUCCEEDED,\n                selected_model_config_id=\"producer-model\",\n                selected_provider_id=\"producer-provider\",\n                capability_ids=(capability_id,),\n                capability_versions={capability_id: \"1\"},\n                result_ids=(result_id,),\n            )\n        )\n        service = VerificationService(require_canonical_subjects=True)\n        completion = VerificationCompletionAuthority(service)\n        policy = service.register_policy(\n            VerificationPolicy(\n                name=\"derived scope\",\n                stages=(VerificationStage(\"review\", VerifierKind.HUMAN),),\n                independence=ReviewerIndependence(human_reviewer_must_differ=True),\n            )\n        )\n        resolver = KernelFileVerificationEvidenceResolver(\n            kernel,\n            repository,\n            LocalFileProvider(tmp_path / \"files\", tmp_path / \"files.sqlite3\"),\n            agents,\n        )\n        runtime = CanonicalVerificationRuntime(completion, resolver)\n        request = await runtime.request_verification(\n            task_id=task.task_id,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n            stage_id=\"review\",\n            subject_type=\"result\",\n            subject_id=result_id,\n            correlation_id=task.task_id,\n        )\n        assert request.project_id == project_id\n        assert request.capability_ids == (capability_id,)\n        assert request.run_id == run.run_id\n        assert request.producer is not None\n        assert request.producer.agent_id == agent_id\n        assert request.producer.agent_revision == 3\n        assert request.producer.model_config_id == \"producer-model\"\n        assert request.producer.provider_id == \"producer-provider\"\n\n    asyncio.run(scenario())\n\n\ndef test_reverification_derives_task_and_replaces_producer_context(tmp_path) -> None:\n    class FakeEvidence:\n        def __init__(self, task_id: str, first: VerificationSubject, second: VerificationSubject):\n            self.task_id = task_id\n            self.first = first\n            self.second = second\n            self.use_second = False\n\n        async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):\n            assert task_id == self.task_id\n            return self.second if self.use_second else self.first\n\n        async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):\n            from ai_multi_agent_platform.verification import ProducerIdentity, VerificationEvidenceContext\n\n            assert task_id == self.task_id\n            subject = self.second if self.use_second else self.first\n            suffix = \"b\" if self.use_second else \"a\"\n            return VerificationEvidenceContext(\n                task_id=task_id,\n                subject=subject,\n                run_id=new_id(\"run\"),\n                project_id=None,\n                capability_ids=(),\n                producer=ProducerIdentity(\n                    actor_ref=f\"agent:producer-{suffix}\",\n                    agent_id=new_id(\"agent\"),\n                    agent_revision=1,\n                    model_config_id=f\"model-{suffix}\",\n                    provider_id=f\"provider-{suffix}\",\n                ),\n            )\n\n        async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):\n            return artifact_ids\n\n    task_id = new_id(\"task\")\n    first_result = new_id(\"result\")\n    second_result = new_id(\"result\")\n    first = VerificationSubject(\"result\", first_result, \"1\", \"sha256:first\")\n    second = VerificationSubject(\"result\", second_result, \"2\", \"sha256:second\")\n    service = VerificationService(require_canonical_subjects=True)\n    completion = VerificationCompletionAuthority(service)\n    policy = service.register_policy(\n        VerificationPolicy(\n            name=\"repair producer replacement\",\n            stages=(VerificationStage(\"review\", VerifierKind.HUMAN),),\n            max_repair_attempts=1,\n        )\n    )\n    evidence = FakeEvidence(task_id, first, second)\n    runtime = CanonicalVerificationRuntime(completion, evidence)\n\n    async def scenario() -> None:\n        initial = await runtime.request_verification(\n            task_id=task_id,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n            stage_id=\"review\",\n            subject_type=\"result\",\n            subject_id=first_result,\n            correlation_id=task_id,\n        )\n        service.record_human_review(\n            initial.verification_id,\n            reviewer_ref=\"user:reviewer\",\n            outcome=VerificationOutcome.NEEDS_CHANGES,\n        )\n        first_producer = initial.producer\n        assert first_producer is not None\n        evidence.use_second = True\n        repaired = await runtime.request_reverification_after_repair(\n            initial.verification_id,\n            subject_type=\"result\",\n            subject_id=second_result,\n            correlation_id=task_id,\n        )\n        assert repaired.task_id == task_id\n        assert repaired.producer is not None\n        assert repaired.producer.model_config_id == \"model-b\"\n        assert repaired.producer.provider_id == \"provider-b\"\n        assert repaired.producer.actor_ref != first_producer.actor_ref\n\n    asyncio.run(scenario())\n''', encoding="utf-8")


def patch_docs() -> None:
    path = "docs/VERIFICATION_HARDENING.md"
    text = read(path)
    addition = '''\n## Authority-integrity hardening\n\nThe production-shaped composition now runs `SqliteVerificationService` in strict canonical-subject mode. Raw caller-supplied `VerificationSubject` requests are rejected there; `CanonicalVerificationRuntime` is the supported request boundary and obtains an internal canonical-subject permit only after evidence resolution. The low-level provider-neutral service remains permissive by default for isolated adapters/tests.\n\n`VerificationEvidenceContext` derives Task project scope and, when a canonical AgentRun produced the reviewed Result/Artifact, the exact producer Agent revision, selected model/provider and capability IDs. Canonical request creation no longer accepts those facts from callers. Reverification derives the Task from the previous immutable request and resolves producer context again for the repaired revision, preventing cross-Task rebinding and stale producer provenance.\n\nRepair execution uses a stable key `(source verification ID, repair attempt)`. A caller retry with a different idempotency key therefore reuses the same canonical Plan/Run; durable kernel history is checked before any new repair mutation.\n\nCompletion assessment scans every stage for critical failure before returning an earlier incomplete-stage `waiting` result. Distinct Agent reviewer quorum is keyed by Agent ID rather than revision-specific verifier text, so two revisions of one Agent do not count as two independent reviewers.\n'''
    if "## Authority-integrity hardening" not in text:
        text += addition
    write(path, text)


def main() -> None:
    patch_service()
    patch_persistence()
    patch_gate()
    patch_evidence()
    patch_exports()
    patch_repair()
    patch_deployment()
    patch_existing_tests()
    append_regression_tests()
    create_authority_integrity_tests()
    patch_docs()


if __name__ == "__main__":
    main()
