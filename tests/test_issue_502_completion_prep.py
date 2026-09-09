from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities.types import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    InvocationStatus,
    InvocationTrace,
)
from ai_multi_agent_platform.context import ContextEntryRole, ContextFreshness
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repository_intelligence.context_funnel import (
    RepositoryContextCaller,
    RepositoryContextFunnel,
    RepositoryContextRequest,
)
from ai_multi_agent_platform.repository_intelligence.evaluation_matrix import (
    RepositoryIntelligenceEvaluationObservation,
    RepositoryIntelligenceMetric,
    compare_repository_intelligence_observations,
)
from ai_multi_agent_platform.repository_intelligence.fallback import (
    RepositoryIntelligenceFallbackInvoker,
)
from ai_multi_agent_platform.repository_intelligence.models import (
    RepositoryIntelligenceFreshness,
)
from ai_multi_agent_platform.repository_intelligence.projectatlas import (
    PROJECTATLAS_ARCHIVE_SHA256,
    PROJECTATLAS_RUNTIME_VERSION,
)
from ai_multi_agent_platform.repository_intelligence.projectatlas_adapter import (
    ProjectAtlasContainmentEvidence,
    ProjectAtlasSourceBinding,
)
from ai_multi_agent_platform.repository_intelligence.projectatlas_resources import (
    PROJECTATLAS_PILOT_PEAK_RSS_BYTES,
    PROJECTATLAS_PILOT_STATE_BYTES,
    projectatlas_resource_profile,
    with_projectatlas_admission_bounds,
)
from ai_multi_agent_platform.repository_intelligence.resources import (
    RepositoryIntelligenceWorkload,
)
from ai_multi_agent_platform.repository_intelligence.search_bridge import (
    RepositoryIntelligenceSearchFederator,
    RepositorySearchCaller,
    RepositorySearchScope,
)
from ai_multi_agent_platform.search import SearchMode, SearchQuery

_REVISION = "a" * 40


def _operation_and_trace() -> tuple[OperationContext, InvocationTrace]:
    project_id = new_id("project")
    operation = OperationContext(
        correlation_id="issue-502-completion-prep",
        owner_type="user",
        owner_id=new_id("user"),
        project_id=project_id,
    )
    trace = InvocationTrace(
        correlation_id=operation.correlation_id,
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        project_id=project_id,
    )
    return operation, trace


def _result(
    request: CapabilityInvocation,
    *,
    provider_id: str,
    output: object,
) -> CapabilityInvocationResult:
    return CapabilityInvocationResult(
        invocation_id=request.invocation_id,
        capability_id=request.capability_id,
        capability_version="1.0",
        provider_id=provider_id,
        status=InvocationStatus.SUCCEEDED,
        output=output,  # type: ignore[arg-type]
    )


class _UnavailablePort:
    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        del request
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "candidate index is stale/unavailable",
            provider_id="candidate.repository-intelligence",
        )


class _BaselinePort:
    def __init__(self) -> None:
        self.calls: list[CapabilityInvocation] = []

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        self.calls.append(request)
        return _result(
            request,
            provider_id="platform.repository-intelligence.baseline",
            output={"fallback": True},
        )


def test_runtime_unavailable_provider_falls_back_to_baseline_unless_required() -> None:
    async def scenario() -> None:
        operation, trace = _operation_and_trace()
        baseline = _BaselinePort()
        coordinator = RepositoryIntelligenceFallbackInvoker(_UnavailablePort(), baseline)
        request = CapabilityInvocation(
            invocation_id="issue-502-fallback",
            capability_id="repository.text_search",
            arguments={
                "repository_id": new_id("external_resource"),
                "revision": "HEAD",
                "query": "needle",
            },
            context=operation,
            trace=trace,
            granted_permissions=frozenset({"repository.text_search"}),
        )

        outcome = await coordinator.invoke(request)
        assert outcome.fallback_used is True
        assert outcome.preferred_error_code == ErrorCode.UNAVAILABLE.value
        assert outcome.result.provider_id == "platform.repository-intelligence.baseline"
        assert len(baseline.calls) == 1

        with pytest.raises(ContractError) as required:
            await coordinator.invoke(request, provider_specific_required=True)
        assert required.value.code is ErrorCode.UNAVAILABLE
        assert len(baseline.calls) == 1

    asyncio.run(scenario())


class _RepositoryFixturePort:
    def __init__(self, *, workspace_id: str | None = None) -> None:
        self.workspace_id = workspace_id
        self.calls: list[CapabilityInvocation] = []

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        self.calls.append(request)
        provenance: dict[str, object] = {
            "repository_id": request.arguments["repository_id"],
            "requested_revision": request.arguments["revision"],
            "resolved_revision": _REVISION,
            "intelligence_provider_id": "candidate.repository-intelligence",
            "freshness": "live_workspace" if self.workspace_id else "live_revision",
        }
        if self.workspace_id:
            provenance["workspace"] = {
                "workspace_id": self.workspace_id,
                "workspace_snapshot_id": "snapshot-502",
                "materialization_id": "materialization-502",
                "dirty": True,
            }

        if request.capability_id == "repository.text_search":
            return _result(
                request,
                provider_id="candidate.repository-intelligence",
                output={
                    "hits": [
                        {"path": "src/target.py", "line": 12, "text": "needle"},
                        {"path": "src/other.py", "line": 4, "text": "needle"},
                    ],
                    "provenance": provenance,
                },
            )
        if request.capability_id == "repository.source_slice":
            path = request.arguments["path"]
            return _result(
                request,
                provider_id="candidate.repository-intelligence",
                output={
                    "path": path,
                    "lines": [
                        {"line": 11, "text": "def target():"},
                        {"line": 12, "text": "    return 'needle'"},
                    ],
                    "provenance": provenance,
                },
            )
        raise AssertionError(f"unexpected capability {request.capability_id}")


def test_context_funnel_uses_bounded_exact_source_slices_with_provenance() -> None:
    async def scenario() -> None:
        operation, trace = _operation_and_trace()
        workspace_id = new_id("workspace")
        port = _RepositoryFixturePort(workspace_id=workspace_id)
        funnel = RepositoryContextFunnel(port)
        report = await funnel.collect(
            RepositoryContextRequest(
                repository_id=new_id("external_resource"),
                query="needle",
                revision="HEAD",
                max_hits=10,
                max_slices=1,
                slice_radius_lines=2,
            ),
            RepositoryContextCaller(
                context=operation,
                trace=trace,
                granted_permissions=frozenset(
                    {"repository.text_search", "repository.source_slice"}
                ),
            ),
        )

        assert report.tool_calls == 2
        assert report.selected_hits == 1
        assert report.source_slices == 1
        assert report.broad_full_file_reads == 0
        candidate = report.candidates[0]
        assert candidate.role is ContextEntryRole.EVIDENCE
        assert candidate.freshness is ContextFreshness.CURRENT
        assert candidate.workspace_id == workspace_id
        assert candidate.source.revision == _REVISION
        assert candidate.metadata["provider_summary_authority"] is False
        assert candidate.metadata["source_slice"] is True
        assert candidate.metadata["path"] == "src/target.py"

    asyncio.run(scenario())


def test_search_federation_is_scoped_ephemeral_and_post_authorized() -> None:
    async def scenario() -> None:
        operation, trace = _operation_and_trace()
        workspace_id = new_id("workspace")
        repository_id = new_id("external_resource")
        port = _RepositoryFixturePort(workspace_id=workspace_id)
        federator = RepositoryIntelligenceSearchFederator(port)

        async def authorize(result) -> bool:  # type: ignore[no-untyped-def]
            return result.title.startswith("src/target.py:")

        page = await federator.search(
            RepositorySearchScope(
                repository_id=repository_id,
                revision="HEAD",
                workspace_id=workspace_id,
            ),
            SearchQuery(
                text="needle",
                project_id=operation.project_id,
                workspace_id=workspace_id,
                mode=SearchMode.KEYWORD,
                limit=10,
            ),
            RepositorySearchCaller(
                context=operation,
                trace=trace,
                granted_permissions=frozenset({"repository.text_search"}),
            ),
            authorize,
        )

        assert page.total == 1
        assert len(page.items) == 1
        result = page.items[0]
        assert result.access == "authorized"
        assert result.workspace_id == workspace_id
        assert result.provider == "candidate.repository-intelligence"
        assert result.provenance["repository_id"] == repository_id
        assert result.provenance["resolved_revision"] == _REVISION
        assert result.provenance["derived_search_result"] is True

    asyncio.run(scenario())


def test_projectatlas_source_binding_and_containment_fail_closed() -> None:
    binding = ProjectAtlasSourceBinding(
        repository_id=new_id("external_resource"),
        requested_revision="HEAD",
        resolved_revision=_REVISION,
        source_root=Path("/srv/workspaces/issue-502"),
        database_path=Path("/var/lib/ai-agent/projectatlas/issue-502.sqlite3"),
        freshness=RepositoryIntelligenceFreshness.LIVE_WORKSPACE,
        workspace_id=new_id("workspace"),
        workspace_snapshot_id="snapshot-502",
        materialization_id="materialization-502",
        source_content_checksum="sha256:workspace-502",
        dirty=True,
    )
    provenance = binding.provenance_details()
    assert provenance["resolved_revision"] == _REVISION
    assert provenance["freshness"] == "live_workspace"
    assert provenance["dirty"] is True

    incomplete = ProjectAtlasContainmentEvidence(
        runtime_version=PROJECTATLAS_RUNTIME_VERSION,
        artifact_sha256=PROJECTATLAS_ARCHIVE_SHA256,
        source_read_only=True,
        provider_state_outside_source=True,
        secrets_stripped=True,
        network_egress_denied=False,
        no_new_privileges=True,
        process_boundary="linux-seccomp-pilot",
        platform="linux-x86_64",
        evidence_ref="issue-502-pending-aggregate-validation",
    )
    assert incomplete.source_operations_ready is False
    with pytest.raises(ContractError) as blocked:
        incomplete.require_source_operations_ready()
    assert blocked.value.code is ErrorCode.UNAVAILABLE

    ready = ProjectAtlasContainmentEvidence(
        runtime_version=PROJECTATLAS_RUNTIME_VERSION,
        artifact_sha256=PROJECTATLAS_ARCHIVE_SHA256,
        source_read_only=True,
        provider_state_outside_source=True,
        secrets_stripped=True,
        network_egress_denied=True,
        no_new_privileges=True,
        process_boundary="linux-seccomp-pilot",
        platform="linux-x86_64",
        evidence_ref="issue-502-aggregate-validation",
    )
    assert ready.source_operations_ready is True


def test_projectatlas_heavy_work_remains_unadmitted_until_measured_bounds_exist() -> None:
    profile = projectatlas_resource_profile()
    query = profile.envelope(RepositoryIntelligenceWorkload.BOUNDED_QUERY)
    rebuild = profile.envelope(RepositoryIntelligenceWorkload.FULL_REBUILD)

    assert query.observed_peak_rss_bytes == PROJECTATLAS_PILOT_PEAK_RSS_BYTES
    assert query.observed_state_bytes == PROJECTATLAS_PILOT_STATE_BYTES
    assert query.admission_complete is False
    assert rebuild.admission_complete is False

    query_requirements = query.to_job_requirements(capability_refs=("repository.text_search",))
    assert query_requirements.network_required is False

    with pytest.raises(ContractError) as unmeasured_rebuild:
        rebuild.to_job_requirements(capability_refs=("repository.refresh",))
    assert unmeasured_rebuild.value.code is ErrorCode.UNAVAILABLE

    measured = with_projectatlas_admission_bounds(
        profile,
        workload=RepositoryIntelligenceWorkload.FULL_REBUILD,
        cpu_cores_min=2.0,
        ram_min_bytes=2 * 1024**3,
        storage_min_bytes=4 * 1024**3,
        evidence_ref="aggregate-benchmark:representative-repository-v1",
    )
    requirements = measured.envelope(
        RepositoryIntelligenceWorkload.FULL_REBUILD
    ).to_job_requirements(capability_refs=("repository.refresh",))
    assert requirements.cpu_cores_min == 2.0
    assert requirements.ram_min_bytes == 2 * 1024**3


def test_evaluation_matrix_preserves_unmeasured_values_and_comparability() -> None:
    baseline = RepositoryIntelligenceEvaluationObservation(
        provider_id="platform.repository-intelligence.baseline",
        fixture_id="fixture-502",
        source_revision=_REVISION,
        environment_ref="aggregate-env-v1",
        task_success=True,
        agent_tool_calls=8,
        broad_full_file_reads=3,
        query_latency_ms=12.0,
        paid_service_required=False,
    )
    candidate = RepositoryIntelligenceEvaluationObservation(
        provider_id="candidate.repository-intelligence",
        fixture_id="fixture-502",
        source_revision=_REVISION,
        environment_ref="aggregate-env-v1",
        task_success=True,
        agent_tool_calls=4,
        broad_full_file_reads=0,
        query_latency_ms=8.0,
        paid_service_required=False,
    )

    comparison = compare_repository_intelligence_observations(baseline, candidate)
    assert comparison.comparable is True
    deltas = {item.metric: item for item in comparison.metrics}
    assert deltas[RepositoryIntelligenceMetric.AGENT_TOOL_CALLS].delta == -4.0
    assert deltas[RepositoryIntelligenceMetric.BROAD_FULL_FILE_READS].delta == -3.0
    assert deltas[RepositoryIntelligenceMetric.QUERY_LATENCY_MS].delta == -4.0
    assert RepositoryIntelligenceMetric.SYMBOL_CORRECTNESS.value in candidate.missing_metric_names
    assert candidate.to_json()["metrics"]["symbol_correctness"] is None

    incompatible = RepositoryIntelligenceEvaluationObservation(
        provider_id="other",
        fixture_id="fixture-502",
        source_revision="b" * 40,
        environment_ref="aggregate-env-v1",
    )
    rejected = compare_repository_intelligence_observations(baseline, incompatible)
    assert rejected.comparable is False
    assert "source_revision differs" in rejected.non_comparability_reasons
