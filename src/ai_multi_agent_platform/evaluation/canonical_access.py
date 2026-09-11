"""Trusted platform-domain access to canonical Evaluation configuration completion."""

from __future__ import annotations

from .models import ConfigurationSnapshot
from .service import EvaluationService


class CanonicalEvaluationAccess:
    """Narrow seam for domains that must bind evidence to #19's exact run configuration.

    EvaluationRunner owns the canonical completion of a caller-supplied ConfigurationSnapshot with
    suite/evaluator/runtime references. This package-owned access object exposes only that immutable
    completion step so other platform domains do not duplicate #19 configuration semantics.
    """

    def __init__(self, evaluation: EvaluationService) -> None:
        self.evaluation = evaluation

    def complete_snapshot(
        self,
        *,
        suite_ref: str,
        snapshot: ConfigurationSnapshot,
    ) -> ConfigurationSnapshot:
        suite = self.evaluation.get_suite(suite_ref)
        return self.evaluation._runner._complete_snapshot(  # noqa: SLF001
            suite=suite,
            snapshot=snapshot,
            regression_policy=None,
            aggregation_policy=None,
        )
