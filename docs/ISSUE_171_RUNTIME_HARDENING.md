# Issue #171 runtime hardening

PR #389 landed the core post-#76 integrations, but the original #171 Definition of Done also requires real source-runtime proofs rather than synthetic compatibility-only tests.

This hardening pass keeps #76 as the sole accounting authority and adds direct regressions for:

- completed #14 `DistributedRuntime` heartbeat/resource facts flowing through #16 into #76;
- completed #33 `AgentRuntime`/`AgentRunRecord` revision provenance across later definition edits and orchestrator replacement;
- Agent/Team trace identity surviving a Worker transport boundary and reaching #76 attribution;
- #87 Team-scoped aggregate grants not widening into Organization-wide access;
- aggregate projections removing person-level Task/Run/Agent identifiers;
- Workspace archive/latest-snapshot/deletion current-gauge semantics.

No second accounting store, budget model, or source-domain lifecycle authority is introduced.
