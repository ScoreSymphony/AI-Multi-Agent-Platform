# Research single-node integration (#589)

Research is part of the standard ``build_single_node_deployment(...)`` composition. The deployment
opens ``db/research.sqlite3`` through ``SqliteResearchRepository``, constructs ``ResearchService``
with the existing #15 ``AuthorizationGate``, registers the Search-aware Research Control Plane
surface and exposes the long-lived service as ``SingleNodeDeployment.research``.

``compose_single_node_research(...)`` remains only as an idempotent compatibility accessor for the
standard store. Supplying an explicit alternate database path still creates a separate isolated
composition for compatibility/tests.

The Research quality suite is a versioned opt-in suite because ``canonical_research_quality_suite``
is bound to one exact Research Item identity. Research bundle codec/export support remains available
in the shared #79 portability composition only through an explicit owner-domain opt-in. The standard
single-node northbound ``portability.export`` path leaves Research disabled because the generic #79
export contract does not carry caller owner scope into per-resource loaders; exposing Research there
would bypass its fail-closed owner boundary. Owner-domain import remains explicit so local #86
Verification authority is revalidated rather than trusted from serialized metadata.

Acceptance coverage lives under ``tests/contract/research`` and includes public single-node
composition/restart/Search, explicit opt-in shared portability export, deterministic Research
evaluation, Research Team role separation, Decision provenance and fail-closed imported Verification
validation.
