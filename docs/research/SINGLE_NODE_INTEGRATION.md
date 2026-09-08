# Research single-node integration handoff (#589)

This active branch prepares the final production-shaped single-node composition without forcing a
concurrent edit into the heavily shared `deployment/single_node.py` integration point.

## Prepared composition

`compose_single_node_research(...)` attaches the canonical Research domain to an already-built
`SingleNodeDeployment` by:

1. opening the durable `database/research.sqlite3` store through `SqliteResearchRepository`;
2. constructing `ResearchService` with the deployment's existing #15 `AuthorizationGate`;
3. registering the Search-aware Research Control Plane surface from the #589 Search slice.

No second Task/Run, authorization, verification, file, artifact or search authority is introduced.

## Aggregate-branch fold-in

When all active branches are consolidated, fold this seam into the standard
`build_single_node_deployment(...)` composition:

- add a long-lived `research: ResearchService` field to `SingleNodeDeployment`;
- create the service from `database_dir / "research.sqlite3"` after `approval_gate` exists;
- register `register_searchable_research_control_plane(control_plane, research)` after the Control
  Plane is constructed;
- return the same Research service on the deployment object;
- keep `compose_single_node_research(...)` either as a compatibility/helper seam or reduce it to an
  idempotent accessor after the central composition is merged.

The aggregate branch should also decide whether the Research quality evaluator from the separate
#589 evaluation slice is registered into deployment-owned evaluation assets by default or remains a
versioned opt-in suite. That policy choice should be made once, after all active evaluation changes
have been combined.

## Prepared acceptance coverage

`tests/contract/research/test_research_single_node_composition.py` prepares coverage for:

- #15-authorized Research creation in the ordinary single-node deployment;
- durable restart through `research.sqlite3`;
- Search registration/rebuild from the restored canonical Research state.

Per-branch CI, CodeQL and smoke execution are intentionally deferred. This branch is intended to be
validated only after it is included in the unified integration branch.
