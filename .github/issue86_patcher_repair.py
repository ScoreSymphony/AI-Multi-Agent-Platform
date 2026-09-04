from __future__ import annotations

import runpy
from pathlib import Path

patcher = Path(__file__).with_name("issue86_final_attestation_patch.py")
text = patcher.read_text()
old = '''# Insert invalidation at the two return sites by operation-specific anchors.\nreplace_once(\n    "src/ai_multi_agent_platform/kernel/kernel.py",\n    "            source=source,\\n"\n    "        )\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def attach_result(\\n",\n    "            source=source,\\n"\n    "        )\\n"\n    "        self._invalidate_completion_subject(task_id)\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def attach_result(\\n",\n)\nreplace_once(\n    "src/ai_multi_agent_platform/kernel/kernel.py",\n    "            source=source,\\n"\n    "        )\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def recover_task(\\n",\n    "            source=source,\\n"\n    "        )\\n"\n    "        self._invalidate_completion_subject(task_id)\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def recover_task(\\n",\n)\n'''
new = '''# Insert invalidation at the exact final attach_artifact/attach_result return sites.\nkernel_path = ROOT / "src/ai_multi_agent_platform/kernel/kernel.py"\nkernel_text = kernel_path.read_text()\nfor method_name, next_method in (("attach_artifact", "attach_result"), ("attach_result", "recover_task")):\n    start = kernel_text.index(f"    async def {method_name}(")\n    end = kernel_text.index(f"    async def {next_method}(", start)\n    block = kernel_text[start:end]\n    needle = "        return await self.get_task(task_id)\\n"\n    pos = block.rfind(needle)\n    if pos < 0:\n        raise SystemExit(f"final return anchor missing in {method_name}")\n    block = (\n        block[:pos]\n        + "        self._invalidate_completion_subject(task_id)\\n"\n        + block[pos:]\n    )\n    kernel_text = kernel_text[:start] + block + kernel_text[end:]\nkernel_path.write_text(kernel_text)\n'''
if old not in text:
    raise SystemExit("kernel anchor block not found in patcher")
patcher.write_text(text.replace(old, new, 1))
runpy.run_path(str(patcher), run_name="__main__")

# Complete imports required by generated focused regressions.
authority_test = Path("tests/test_issue_86_authority_integrity.py")
authority = authority_test.read_text()
authority = authority.replace(
    "from ai_multi_agent_platform.domain import OwnerRef, new_id\n",
    "from ai_multi_agent_platform.domain import OwnerRef, TaskStatus, new_id\n",
    1,
)
authority = authority.replace(
    "from ai_multi_agent_platform.verification import (\n",
    "from ai_multi_agent_platform.verification import (\n    CompletionState,\n",
    1,
)
# The completion requirement must exist before the task-level Run terminalizes,
# otherwise the kernel legitimately marks the task SUCCEEDED before there is
# anything for Verification to gate.
needle = '''        task = await kernel.create_task(\n            idempotency_key="invalidate:create",\n            title="Invalidate old review",\n            objective="New output must require new verification",\n            owner_type="user",\n            owner_id="issue-86",\n        )\n        await kernel.ready_task(idempotency_key="invalidate:ready", task_id=task.task_id)\n'''
replacement = '''        task = await kernel.create_task(\n            idempotency_key="invalidate:create",\n            title="Invalidate old review",\n            objective="New output must require new verification",\n            owner_type="user",\n            owner_id="issue-86",\n        )\n        completion.require_task(\n            task_id=task.task_id,\n            policy_id=policy.policy_id,\n            policy_version=policy.version,\n        )\n        await kernel.ready_task(idempotency_key="invalidate:ready", task_id=task.task_id)\n'''
if needle not in authority:
    raise SystemExit("automatic invalidation test anchor missing")
authority = authority.replace(needle, replacement, 1)
authority_test.write_text(authority)

hardening_test = Path("tests/test_issue_86_hardening.py")
hardening = hardening_test.read_text()
if "    VerificationCompletionAuthority,\n" not in hardening:
    hardening = hardening.replace(
        "from ai_multi_agent_platform.verification import (\n",
        "from ai_multi_agent_platform.verification import (\n    VerificationCompletionAuthority,\n",
        1,
    )
raw_human = '''        deployment.verification.record_human_review(\n            request.verification_id,\n            reviewer_ref=f"user:{admin.user_id}:reviewer",\n            outcome=VerificationOutcome.PASS,\n        )\n'''
canonical_human = '''        await deployment.verification_runtime.submit_result(\n            VerificationResult(\n                verification_id=request.verification_id,\n                verifier=VerifierIdentity(\n                    verifier_ref=f"user:{admin.user_id}:reviewer",\n                    kind=VerifierKind.HUMAN,\n                    read_only=True,\n                ),\n                outcome=VerificationOutcome.PASS,\n                subject=request.subject,\n                checks_executed=("human_review",),\n            )\n        )\n'''
if raw_human not in hardening:
    raise SystemExit("single-node raw human review anchor missing")
hardening = hardening.replace(raw_human, canonical_human, 1)
hardening_test.write_text(hardening)

integration_test = Path("tests/test_issue_86_hardening_integration.py")
integration = integration_test.read_text()
raw_integration_review = '''        deployment.verification.record_human_review(\n            canonical_request.verification_id,\n            reviewer_ref=admin.user_id,\n            outcome=VerificationOutcome.PASS,\n        )\n'''
canonical_integration_review = '''        accept_context = RequestContext(\n            request_id="canonical-review-accept",\n            correlation_id=task.task_id,\n            idempotency_key="canonical-review-accept",\n            actor=ActorContext(\n                principal_ref=admin.user_id,\n                owner_type="user",\n                owner_id=admin.user_id,\n                actor_type="human",\n            ),\n        )\n        accepted = await deployment.control_plane.execute_command(\n            accept_context,\n            "verification.accept",\n            canonical_request.verification_id,\n            {},\n        )\n        assert accepted["verification_result"]["outcome"] == "pass"\n'''
if raw_integration_review not in integration:
    raise SystemExit("integration raw human review anchor missing")
integration = integration.replace(raw_integration_review, canonical_integration_review, 1)
integration_test.write_text(integration)

# Strict deterministic Verification uses the same final canonical attestation boundary.
evidence_path = Path("src/ai_multi_agent_platform/verification/evidence.py")
evidence = evidence_path.read_text()
if "from .deterministic import ReferenceDeterministicVerifier\n" not in evidence:
    marker = "from .gate import TaskVerificationRequirement, VerificationCompletionAuthority\n"
    if marker not in evidence:
        raise SystemExit("evidence deterministic import anchor missing")
    evidence = evidence.replace(
        marker,
        "from .deterministic import ReferenceDeterministicVerifier\n" + marker,
        1,
    )
submit_anchor = '''    async def request_reverification_after_repair(\n'''
run_deterministic = '''    async def run_deterministic(\n        self,\n        verification_id: str,\n        verifier: ReferenceDeterministicVerifier,\n    ) -> VerificationResult:\n        request = self._completion.verification.get_request(verification_id)\n        return await self.submit_result(verifier.verify(request))\n\n'''
if run_deterministic not in evidence:
    if submit_anchor not in evidence:
        raise SystemExit("canonical deterministic method anchor missing")
    evidence = evidence.replace(submit_anchor, run_deterministic + submit_anchor, 1)
evidence_path.write_text(evidence)

# Add a strict-mode deterministic regression using the mutable resolver fixture.
hardening = hardening_test.read_text()
if "    DeterministicCheck,\n" not in hardening:
    hardening = hardening.replace(
        "from ai_multi_agent_platform.verification import (\n",
        "from ai_multi_agent_platform.verification import (\n    DeterministicCheck,\n    ReferenceDeterministicVerifier,\n",
        1,
    )
if "test_strict_deterministic_submission_uses_canonical_runtime" not in hardening:
    hardening += r'''


def test_strict_deterministic_submission_uses_canonical_runtime() -> None:
    async def scenario() -> None:
        subject = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="1",
            digest="sha256:deterministic",
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
                name="strict-deterministic",
                stages=(VerificationStage("checks", VerifierKind.DETERMINISTIC),),
            )
        )
        request = await runtime.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="checks",
            subject_type="result",
            subject_id=subject.subject_id,
            correlation_id="strict-deterministic",
        )
        verifier = ReferenceDeterministicVerifier(
            "deterministic:reference",
            (DeterministicCheck("subject-present", lambda item: bool(item.subject.digest), "missing"),),
        )
        result = await runtime.run_deterministic(request.verification_id, verifier)
        assert result.outcome is VerificationOutcome.PASS
        assert service.result_for(request.verification_id) == result

    asyncio.run(scenario())
'''
hardening_test.write_text(hardening)
