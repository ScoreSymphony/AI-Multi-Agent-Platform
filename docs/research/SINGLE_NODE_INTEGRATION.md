# Research single-node integration (#589)

Research is part of the standard ``build_single_node_deployment(...)`` composition. The deployment
opens ``db/research.sqlite3`` through ``SqliteResearchRepository``, constructs ``ResearchService``
with the existing #15 ``AuthorizationGate``, registers the Search-aware Research Control Plane
surface and exposes the long-lived service as ``SingleNodeDeployment.research``.

``compose_single_node_research(...)`` remains only as an idempotent compatibility accessor for the
standard store. Supplying an explicit alternate database path still creates a separate isolated
composition for compatibility/tests.

The Research quality suite is a versioned opt-in suite because ``canonical_research_quality_suite``
is bound to one exact Research Item identity. Research bundle export is registered in the shared
#79 portability composition; owner-domain import remains explicit so local #86 Verification
authority is revalidated rather than trusted from serialized metadata.

Acceptance coverage lives under ``tests/contract/research`` and includes public single-node
composition/restart/Search, shared portability export, deterministic Research evaluation, Research
Team role separation, Decision provenance and fail-closed imported Verification validation.
