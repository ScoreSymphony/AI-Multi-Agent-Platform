"""Public coding-batch coordinator with the #15 merge-readiness seam sealed."""

from __future__ import annotations

from .models import IntegrationCandidate
from .service import CodingBatchCoordinator as _StateCoordinator


class CodingBatchCoordinator(_StateCoordinator):
    """Supported #872 coordinator surface.

    The underlying state machine retains an internal transition primitive for composition tests and
    persistence, but callers cannot assert authorization with a boolean. Productive merge readiness
    must cross :class:`AuthorizedCodingBatchIntegration`, which invokes #15 first.
    """

    def mark_merge_ready(
        self,
        batch_id: str,
        integration_id: str,
    ) -> IntegrationCandidate:
        del batch_id, integration_id
        raise PermissionError(
            "merge readiness requires canonical #15 AuthorizedCodingBatchIntegration"
        )

    def _mark_merge_ready_after_authorization(
        self,
        batch_id: str,
        integration_id: str,
    ) -> IntegrationCandidate:
        """Internal state transition used only after the #15 adapter has enforced its action."""

        return super().mark_merge_ready(
            batch_id,
            integration_id,
            authorization_granted=True,
        )
