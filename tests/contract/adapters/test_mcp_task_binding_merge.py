from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.mcp_tasks import (
    InMemoryMCPTaskBindingStore,
    MCPTaskBinding,
    MCPTaskStatus,
    SqliteMCPTaskBindingStore,
    mark_cancellation_requested,
    mark_cancellation_result,
    mark_input_requests_responded,
)


def _binding(*, offset_seconds: int = 0) -> MCPTaskBinding:
    created_at = datetime(2026, 9, 13, 19, 0, tzinfo=UTC)
    return MCPTaskBinding(
        provider_id="mcp:binding-merge",
        server_id="binding-merge",
        invocation_id="invoke-binding-merge",
        canonical_task_id="task-binding-merge",
        canonical_run_id="run-binding-merge",
        owner_type="user",
        owner_id="user-964",
        project_id="project-binding-merge",
        causation_id="cause-binding-merge",
        correlation_id="corr-binding-merge",
        idempotency_key="idem-binding-merge",
        external_task_id="external-binding-merge",
        external_created_at=created_at,
        latest_status=MCPTaskStatus.WORKING,
        latest_observed_at=created_at + timedelta(seconds=offset_seconds),
        protocol_revision="2026-07-28",
        poll_interval_ms=10,
    )


def _stale_sideband_evidence(binding: MCPTaskBinding) -> MCPTaskBinding:
    candidate = mark_input_requests_responded(binding, ("request-a",))
    candidate = mark_cancellation_requested(candidate)
    return mark_cancellation_result(candidate, acknowledged=True)


@pytest.mark.asyncio
async def test_stale_poll_preserves_new_sideband_evidence_in_memory() -> None:
    store = InMemoryMCPTaskBindingStore()
    initial = _binding()
    await store.bind(initial)

    newer = replace(initial, latest_observed_at=initial.latest_observed_at + timedelta(seconds=5))
    await store.save(newer)

    merged = await store.save(_stale_sideband_evidence(initial))

    assert merged.latest_observed_at == newer.latest_observed_at
    assert merged.latest_status is MCPTaskStatus.WORKING
    assert merged.responded_input_keys == ("request-a",)
    assert merged.cancellation_requested_at is not None
    assert merged.cancellation_acknowledged is True


@pytest.mark.asyncio
async def test_stale_sideband_evidence_does_not_regress_sqlite_terminal_state(
    tmp_path: Path,
) -> None:
    store = SqliteMCPTaskBindingStore(tmp_path / "mcp-task-bindings.sqlite3")
    initial = _binding()
    try:
        await store.bind(initial)
        terminal = replace(
            initial,
            latest_status=MCPTaskStatus.COMPLETED,
            latest_observed_at=initial.latest_observed_at + timedelta(seconds=5),
            terminal_payload_digest="terminal-digest",
        )
        await store.save(terminal)

        merged = await store.save(_stale_sideband_evidence(initial))

        assert merged.latest_status is MCPTaskStatus.COMPLETED
        assert merged.latest_observed_at == terminal.latest_observed_at
        assert merged.terminal_payload_digest == "terminal-digest"
        assert merged.responded_input_keys == ("request-a",)
        assert merged.cancellation_requested_at is not None
        assert merged.cancellation_acknowledged is True
    finally:
        store.close()
