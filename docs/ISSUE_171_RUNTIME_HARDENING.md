# Issue #171 runtime hardening

PR #389 landed the core post-#76 integrations, but the original #171 Definition of Done also requires real source-runtime proofs rather than synthetic compatibility-only tests.

This hardening pass keeps #76 as the sole accounting authority and adds direct regressions for:

- completed #14 `DistributedRuntime` heartbeat/resource facts flowing through #16 into #76;
- completed #33 `AgentRuntime`/`AgentRunRecord` revision provenance across later definition edits and orchestrator replacement;
- Agent/Team trace identity surviving a Worker transport boundary and reaching #76 attribution;
- real #87 Organization aggregate visibility and Team-scoped grants without cross-scope widening;
- aggregate projections removing person-level Task/Run/Agent identifiers;
- raw `usage-records` remaining exact-owner isolated even when `accounting.aggregate.read` grants Organization/Team aggregate visibility;
- budget-threshold Notification preferences, minimal accounting summaries and recipient isolation;
- Workspace archive/latest-snapshot/deletion current-gauge semantics;
- the current Control Plane automatically exposing canonical #76 usage resources when an `accounting_service` is configured and replacing only the read projection with #87 membership-aware visibility when an `organization_service` is also configured.

The Control Plane consumes both accounting resource factories through the shared read-only `Mapping[str, ResourceService]` boundary. This keeps the base and Organization-aware projections substitutable without changing either factory's concrete return type.

The same `AccountingService` instance continues through the existing #75 source composition for threshold attention. The Organization layer does not create another store, budget model, event stream or accounting authority.

Single-node deployment activation of Accounting and Organization services is intentionally not introduced here: the deployment currently constructs neither service. #171 supplies and verifies the canonical combined Control Plane seam without silently changing which optional domains the deployment enables.
