# CLI Agent and Team inspection

Issue: #38  
Owning domain: #33

The Agent/Team domain registers its northbound resources through the canonical Control Plane extension registry. The CLI discovers those resources from `/api/v1/openapi.json` before issuing any resource request; it does not import the Agent repository, runtime, orchestrator mapping, or persistence backend.

## Read-only administrative inspection

```bash
platform extension list agents
platform extension show agents AGENT_ID

platform extension list agent-teams
platform extension show agent-teams TEAM_ID

platform extension list agent-runs
platform extension show agent-runs AGENT_RUN_ID
```

The `agents` response includes the current immutable revision. That revision exposes the canonical Agent profile, including enabled state, model requirements, capability allow/deny/constraints, memory scopes/config references, knowledge-source references, workspace defaults, policy hooks, resource hints and public metadata.

The `agent-teams` response includes the current immutable Team revision, member Agent/revision references, roles/delegation, shared capabilities, coordination/parallelism limits, unavailable-member policy, enabled state and public metadata.

`agent-runs` exposes canonical runtime records when the #33 runtime is composed.

## Safety and absence behavior

These inspection commands are GET-only. They use the registered `/api/v1/agents`, `/api/v1/agent-teams` and `/api/v1/agent-runs` collections after confirming those collections in the Control Plane OpenAPI extension manifest.

If #33 is not registered in the target Control Plane, the CLI fails at extension discovery and performs no repository/runtime/backend fallback.

The Agent domain also owns canonical create/update/clone/rollback/start commands. This slice intentionally does not wrap those mutation commands in additional CLI shortcuts; mutations remain available only through explicitly designed canonical API flows with their existing authorization/idempotency requirements. A future dedicated mutation UX should add confirmation/approval semantics rather than bypass the generic Control Plane command boundary.
