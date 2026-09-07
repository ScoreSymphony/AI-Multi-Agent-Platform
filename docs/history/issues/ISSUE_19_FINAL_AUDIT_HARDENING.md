# Issue #19 post-merge hardening audit

This note records the narrow hardening work discovered after PR #367 had already closed issue #19. It supplements `ISSUE_19_COMPLETION.md`; it does not introduce a new Evaluation architecture or broaden the issue into later marketplace/fixture-distribution work.

## Why #19 was reopened

A strict code-level audit of the merged product found three implementation details that were weaker than the final completion language:

1. deployment-owned directory fixtures were joined directly to the configured fixture root and therefore did not reject parent traversal, absolute paths or symlink-resolved file escapes;
2. Agent-target validation proved that requested capabilities were present, but did not prove equality with the complete effective capability set resolved by `AgentRuntime`;
3. EvaluationSuite portability emitted capability and fixture dependencies, while the production single-node portability composition did not provide its canonical CapabilityRegistry or safe local fixture-existence seam to import preview.

## Fixture confinement

`DirectoryEvaluationFixtureResolver` now uses the platform security helper `resolve_within(...)` for both the fixture directory and every materialized file.

The resolver therefore fails closed for:

- absolute fixture paths;
- explicit `..` traversal;
- fixture-directory symlinks that resolve outside the configured root;
- files/symlinks found inside a nominally valid fixture directory that resolve outside that fixture directory.

The same confined lookup is exposed as `fixture_exists(...)` for portability dependency checks. Portability never receives a second, weaker path-existence implementation.

This remains filesystem confinement rather than a claim of hostile-code sandboxing; the existing platform security/TOCTOU guidance still applies.

## Exact Agent/model/capability target validation

An Evaluation target's `capability_ids` are requests into the canonical Agent capability policy, not necessarily the complete runtime set. An Agent revision can also contribute required capabilities.

The product now resolves the expected `AgentExecutionSpec` server-side through the same `AgentRuntime.prepare_agent(...)` contract used before execution. After execution, `AgentTargetValidatingCaseExecutor` requires the recorded `AgentRun` to match that resolved spec for:

- Agent ID and revision;
- selected model configuration;
- selected provider;
- the complete effective capability ID tuple;
- the complete capability ID -> version mapping.

Extra capabilities and capability-version drift therefore fail closed. The target snapshot enricher reuses the same resolution helper so snapshot identity and post-run validation cannot silently use different target-resolution rules.

## EvaluationSuite portability dependencies

`build_agent_portability_workflow(...)` can now receive the canonical `CapabilityRegistry`. Capability dependencies are checked through registry inventory/resolution, including the exact/minimum/maximum version forms already emitted by first-party portability codecs. Unsupported version expressions fail closed rather than being guessed.

The single-node composition supplies:

- its real `CapabilityRegistry` for capability dependency resolution;
- `DirectoryEvaluationFixtureResolver.fixture_exists` for already-provisioned local fixture dependencies.

This does **not** make EvaluationFixture a portable owned resource. A fixture referenced by an imported suite must already exist locally unless a future owning-domain portable EvaluationFixture implementation is added. Missing fixtures therefore continue to fail preview, preserving the separation established by #79 and PR #367.

## Regression coverage

Focused tests cover:

- parent and absolute fixture-path escape rejection;
- symlink-file escape rejection during materialization;
- rejection of an extra effective capability;
- rejection of capability-version drift;
- successful validation of the exact server-resolved capability set and versions;
- recognition of an existing capability dependency by portability.

The repository's full required CI remains authoritative for integration with the rest of the platform.
