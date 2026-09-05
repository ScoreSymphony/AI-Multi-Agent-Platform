from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.security.authorization import RiskClassification
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    SqliteVerificationService,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)


def test_risk_classification_and_self_verification_rules_survive_sqlite_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification.sqlite3"
    service = SqliteVerificationService(database)
    policy = service.register_policy(
        VerificationPolicy(
            name="durable high-risk self-verification policy",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            risk_classification=RiskClassification.HIGH,
            independence=ReviewerIndependence(
                forbid_self_verification_risk_classes=(RiskClassification.HIGH,),
            ),
        )
    )

    restored = SqliteVerificationService(database)
    restored_policy = restored.get_policy(policy.policy_id, policy.version)

    assert restored_policy == policy
    assert restored_policy.risk_classification is RiskClassification.HIGH
    assert restored_policy.independence.forbid_self_verification_risk_classes == (
        RiskClassification.HIGH,
    )
