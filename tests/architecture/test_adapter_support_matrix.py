from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "docs" / "ADAPTER_SUPPORT_MATRIX.toml"
POLICY_PATH = ROOT / "docs" / "ADAPTER_SUPPORT_MATRIX.md"

REQUIRED_BOUNDARIES = {
    "planner",
    "orchestrator",
    "executor",
    "model-provider",
    "model-router",
    "capability-provider",
    "mcp-client",
    "browser-provider",
    "file-provider",
    "memory-provider",
    "knowledge-provider",
    "persistence-repository",
    "message-transport",
    "connector-provider",
    "repository-provider",
    "observability-exporter",
    "verification-provider",
    "evaluation-provider",
    "security-evidence-provider",
    "deployment-adapter",
}
REQUIRED_FIELDS = {
    "id",
    "boundary",
    "kind",
    "symbol",
    "source",
    "tier",
    "owner",
    "local_first",
    "mandatory_paid_service",
    "unique_value",
    "overlap",
    "test_burden",
    "doc_burden",
    "security_surface",
    "upstream_health",
    "compatibility",
    "usage",
    "evidence",
    "docs",
    "migration",
}

# Independent reverse-coverage inventory for every audited first-party implementation.
# Adding/removing a concrete implementation behind an audited boundary must update this inventory
# as well as the support matrix; the equality check below prevents one-sided classification drift.
FIRST_PARTY_IMPLEMENTATION_INVENTORY = {
    (
        "planner.reference",
        "src/ai_multi_agent_platform/planning/providers.py",
        "DeterministicReferencePlanner",
    ),
    (
        "planner.model-backed",
        "src/ai_multi_agent_platform/planning/providers.py",
        "ModelBackedPlanner",
    ),
    (
        "orchestrator.reference",
        "src/ai_multi_agent_platform/orchestration/reference.py",
        "ReferenceOrchestrator",
    ),
    (
        "orchestrator.hermes",
        "src/ai_multi_agent_platform/adapters/hermes.py",
        "HermesOrchestrator",
    ),
    (
        "executor.reference",
        "src/ai_multi_agent_platform/execution/reference.py",
        "ReferenceExecutor",
    ),
    (
        "executor.forge",
        "src/ai_multi_agent_platform/adapters/forge.py",
        "ForgeExecutor",
    ),
    (
        "executor.agent-sandbox",
        "src/ai_multi_agent_platform/adapters/agent_sandbox.py",
        "AgentSandboxExecutor",
    ),
    (
        "executor.swe-rex",
        "src/ai_multi_agent_platform/adapters/swe_rex.py",
        "SwerexExecutor",
    ),
    (
        "model.openai-compatible",
        "src/ai_multi_agent_platform/adapters/openai_compatible_streaming.py",
        "OpenAICompatibleModelProvider",
    ),
    (
        "model.litellm",
        "src/ai_multi_agent_platform/adapters/litellm.py",
        "LiteLLMModelProvider",
    ),
    (
        "model.router",
        "src/ai_multi_agent_platform/models/router.py",
        "DeterministicModelRouter",
    ),
    (
        "capability.native",
        "src/ai_multi_agent_platform/capabilities/native.py",
        "NativeEchoProvider",
    ),
    (
        "capability.mcp",
        "src/ai_multi_agent_platform/adapters/mcp.py",
        "MCPToolProvider",
    ),
    (
        "mcp.python-sdk",
        "src/ai_multi_agent_platform/adapters/mcp_sdk.py",
        "MCPPythonSDKClient",
    ),
    (
        "mcp.stateless-http",
        "src/ai_multi_agent_platform/adapters/mcp_stateless.py",
        "MCPStatelessHTTPClient",
    ),
    (
        "browser.stdlib",
        "src/ai_multi_agent_platform/browser/reference.py",
        "StdlibBrowserProvider",
    ),
    (
        "data.file.local",
        "src/ai_multi_agent_platform/data/reference_file.py",
        "LocalFileProvider",
    ),
    (
        "data.memory.local",
        "src/ai_multi_agent_platform/data/reference_memory.py",
        "LocalMemoryProvider",
    ),
    (
        "data.knowledge.local",
        "src/ai_multi_agent_platform/data/reference_knowledge.py",
        "LocalKnowledgeProvider",
    ),
    (
        "persistence.single-node-topology",
        "src/ai_multi_agent_platform/backup/inventory.py",
        "SINGLE_NODE_DURABLE_STORES",
    ),
    (
        "persistence.kernel.in-memory",
        "src/ai_multi_agent_platform/kernel/repository.py",
        "InMemoryKernelRepository",
    ),
    (
        "persistence.kernel.sqlite",
        "src/ai_multi_agent_platform/kernel/sqlite_repository.py",
        "SqliteKernelRepository",
    ),
    (
        "persistence.agent.in-memory",
        "src/ai_multi_agent_platform/agents/repository.py",
        "InMemoryAgentRepository",
    ),
    (
        "persistence.agent.json",
        "src/ai_multi_agent_platform/agents/persistence.py",
        "JsonAgentRepository",
    ),
    (
        "messaging.in-process",
        "src/ai_multi_agent_platform/messaging/reference.py",
        "InProcessMessageTransport",
    ),
    (
        "messaging.tcp",
        "src/ai_multi_agent_platform/messaging/network.py",
        "TcpMessageTransport",
    ),
    (
        "connector.reference",
        "src/ai_multi_agent_platform/connectors/reference.py",
        "ReferenceConnectorProvider",
    ),
    (
        "connector.github-releases",
        "src/ai_multi_agent_platform/connectors/github_releases.py",
        "GitHubReleaseConnectorProvider",
    ),
    (
        "repository.local-git",
        "src/ai_multi_agent_platform/repositories/local_git.py",
        "LocalGitRepositoryProvider",
    ),
    (
        "repository.connector",
        "src/ai_multi_agent_platform/repositories/connector_repository.py",
        "ConnectorRepositoryProvider",
    ),
    (
        "observability.noop",
        "src/ai_multi_agent_platform/observability/exporters.py",
        "NoOpExporter",
    ),
    (
        "observability.in-memory",
        "src/ai_multi_agent_platform/observability/exporters.py",
        "InMemoryExporter",
    ),
    (
        "observability.accounting-bridge",
        "src/ai_multi_agent_platform/observability/integrations.py",
        "AccountingBridgeExporter",
    ),
    (
        "verification.completion",
        "src/ai_multi_agent_platform/verification/gate.py",
        "VerificationCompletionAuthority",
    ),
    (
        "evaluation.deterministic-assertions",
        "src/ai_multi_agent_platform/evaluation/evaluators.py",
        "DeterministicAssertionEvaluator",
    ),
    (
        "evaluation.metric-threshold",
        "src/ai_multi_agent_platform/evaluation/evaluators.py",
        "MetricThresholdEvaluator",
    ),
    (
        "security-evidence.skillspector",
        "src/ai_multi_agent_platform/adapters/skillspector.py",
        "SkillSpectorSecurityEvidenceProvider",
    ),
    (
        "deployment.distributed-lifecycle",
        "src/ai_multi_agent_platform/distributed/lifecycle.py",
        "DistributedLifecycleBackend",
    ),
}


def _matrix() -> dict[str, object]:
    return tomllib.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _implementations() -> list[dict[str, object]]:
    raw = _matrix().get("implementation")
    assert isinstance(raw, list)
    return raw


def test_adapter_support_matrix_covers_issue_904_boundaries() -> None:
    matrix = _matrix()

    assert matrix["schema_version"] == 1
    assert matrix["issue"] == 904
    audited = matrix["audited_boundaries"]
    assert isinstance(audited, list)
    assert REQUIRED_BOUNDARIES <= set(audited)


def test_adapter_support_matrix_matches_explicit_first_party_inventory() -> None:
    classified = {
        (str(entry["id"]), str(entry["source"]), str(entry["symbol"]))
        for entry in _implementations()
    }
    assert classified == FIRST_PARTY_IMPLEMENTATION_INVENTORY


def test_adapter_support_matrix_entries_are_complete_and_unique() -> None:
    matrix = _matrix()
    allowed_tiers = set(matrix["allowed_tiers"])
    entries = _implementations()
    ids: list[str] = []

    for entry in entries:
        missing = REQUIRED_FIELDS - set(entry)
        assert not missing, f"{entry.get('id', '<unknown>')} missing fields: {sorted(missing)}"
        assert entry["tier"] in allowed_tiers
        ids.append(str(entry["id"]))

    assert len(ids) == len(set(ids)), "adapter support ids must be unique"


def test_adapter_support_matrix_points_at_real_implementation_symbols_and_docs() -> None:
    for entry in _implementations():
        source = ROOT / str(entry["source"])
        assert source.is_file(), f"missing source for {entry['id']}: {source.relative_to(ROOT)}"
        source_text = source.read_text(encoding="utf-8")
        assert str(entry["symbol"]) in source_text, (
            f"support entry {entry['id']} names symbol {entry['symbol']} "
            f"that is absent from {source.relative_to(ROOT)}"
        )

        docs = entry["docs"]
        assert isinstance(docs, list) and docs, f"{entry['id']} must name documentation"
        for doc in docs:
            path = ROOT / str(doc)
            assert path.is_file(), f"missing documentation for {entry['id']}: {doc}"


def test_adapter_support_matrix_evidence_targets_are_current() -> None:
    for entry in _implementations():
        evidence = entry["evidence"]
        assert isinstance(evidence, list)
        for target in evidence:
            target_text = str(target)
            if target_text.startswith("workflow:"):
                continue
            path = ROOT / target_text
            assert path.exists(), f"stale evidence for {entry['id']}: {target_text}"


def test_reference_and_supported_entries_have_evidence_and_reference_is_free_baseline() -> None:
    for entry in _implementations():
        tier = entry["tier"]
        if tier in {"reference", "supported"}:
            evidence = entry["evidence"]
            assert isinstance(evidence, list) and evidence, (
                f"{entry['id']} is {tier} but has no conformance/test evidence mapping"
            )

        if tier == "reference":
            assert entry["mandatory_paid_service"] is False, (
                f"reference implementation {entry['id']} cannot require a paid service"
            )


def test_experimental_and_deprecated_entries_cannot_imply_unqualified_support() -> None:
    for entry in _implementations():
        compatibility = str(entry["compatibility"]).lower()
        if entry["tier"] == "experimental":
            assert any(
                marker in compatibility
                for marker in (
                    "no production",
                    "no supported",
                    "not part",
                    "implemented and tested",
                )
            ), f"experimental entry {entry['id']} must explicitly limit its compatibility claim"
        if entry["tier"] == "deprecated":
            migration = str(entry["migration"])
            assert migration and migration.lower() != "n/a", (
                f"deprecated entry {entry['id']} requires an explicit migration/removal plan"
            )


def test_policy_document_mentions_every_first_party_support_entry() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")
    for entry in _implementations():
        assert f"`{entry['id']}`" in policy, f"policy document omits {entry['id']}"
