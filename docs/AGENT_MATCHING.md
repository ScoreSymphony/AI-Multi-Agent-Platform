# Canonical Agent matching

Issue #903 introduces one provider-neutral eligibility and selection boundary for canonical Agent and AgentTeam revisions.

## Responsibility

`AgentMatcher` answers one question: which canonical Agent or AgentTeam revision is eligible and best suited for supplied work requirements?

It does not own Worker/Node placement (#14), model-provider routing (#10), capability-provider selection (#12), authorization authority (#15), planning/decomposition (#439), or runtime execution. Matching consumes canonical metadata and may call a narrow policy filter, but a discovered or selected Agent never gains permission merely by being matched.

`AgentResolver` is the repository-backed application service. It materializes current canonical Agent/Team revisions and delegates eligibility/ranking to `AgentMatcher`. Exact Agent/Team pins bypass unrelated candidate discovery and ranking while still being validated for eligibility.

## Inputs

`AgentMatchingRequirements` supports:

- required and preferred roles;
- required capabilities, exact/ranged versions and required features;
- forbidden capabilities;
- required policy references;
- canonical model requirements including local/self-hosted constraints;
- Project/Workspace/Organization scope;
- exact Agent or Team revision pins;
- producer/reviewer independence exclusions supplied by the owning verification policy;
- explicit preferred canonical IDs and configured `matching_priority` metadata;
- an explicit tie-break policy when callers deliberately want canonical-ID ordering.

Provider-native model IDs, hostnames, worker IDs and orchestrator-private concepts are not matching inputs.

## Filtering and ranking

Candidates are rejected deterministically for disabled state, scope mismatch, missing or denied capabilities, incompatible capability versions/features, incompatible canonical model requirements, policy denial, supplied independence conflicts, or unsupported candidate kind.

Eligible candidates are ranked by explicit preferred ID, preferred-role fit, configured matching priority, and lower unnecessary capability exposure. The default tie policy is `AMBIGUOUS`; equal candidates are never selected by incidental repository iteration order. Callers may opt into `CANONICAL_ID` when deterministic canonical-ID ordering is itself the documented policy.

`AgentMatchResult` contains the selected revision when one exists, the candidate outcomes, structured rejection reasons, and any ambiguous candidate set. Diagnostics intentionally expose safe canonical reasons rather than hidden authorization internals.

## Integrations

### Planning (#439)

Planning translates its already server-resolved/authorized inventory to the shared matcher. Role-only Step assignments are resolved to an exact Agent revision before proposal persistence when exactly one eligible Agent exists. No match or ambiguity remains explicit and is rejected by proposal validation. The current reference execution seam is single-Agent-per-Step, so it does not silently reinterpret a Team as an executable Agent.

### Handoffs (#651)

`CanonicalConsumerRequirementEvaluator` parses late-bound consumer requirements and evaluates the concrete consuming Agent/Team revision through the shared resolver as an exact pin. It does not maintain separate role/capability matching logic and does not scan unrelated Agents when the consumer is already known.

### Reference multi-agent path (#889)

The reference path inherits the same planning resolution and Handoff consumer evaluation boundaries. Future runtime delegation that is not already pinned should call `AgentResolver` rather than introducing provider- or orchestrator-specific selection branches.

## Capability and model boundaries

Capability matching uses canonical capability inventory/version/features. It decides whether the candidate can satisfy the requested capability contract; invocation/provider selection remains with #12.

Model matching tests whether the Agent's model policy and the work requirements have at least one feasible canonical model configuration. It never chooses or invokes a model provider; actual routing remains with #10.

## Authorization

A matcher may receive an `AgentMatchingPolicy` adapter backed by canonical authorization state. The matcher only consumes allow/deny eligibility and emits the safe reason `policy_denied`; it does not copy policy internals or become an authorization authority. Call sites with a pre-filtered authorized inventory may omit that hook.
