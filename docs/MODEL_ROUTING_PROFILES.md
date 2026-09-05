# Durable model-routing profiles

Issue #309 adds the durable policy layer that intentionally sits above the Issue #10 model inventory/router foundation.

## Ownership boundary

The responsibilities stay separate:

```text
ModelProvider -> ModelRegistry -> DeterministicModelRouter
                         ^
                         |
              ModelRoutingProfileRevision
```

`ModelRegistry` remains authoritative for configured model inventory, provider attachment, capabilities, effective health and runtime availability. `DeterministicModelRouter` remains authoritative for selecting a compatible registered model. A routing profile supplies reusable, versioned routing intent to that router; it does not become a second registry or a gateway-specific routing engine.

## Canonical identity and revisions

A profile has a stable canonical ID with the prefix `model_routing_profile_`. Each change creates a new immutable `ModelRoutingProfileRevision`. References that must be reproducible use an exact revision:

```text
model_routing_profile_<uuid>@r3
```

`ModelRoutingProfileRef.parse()` and `.canonical_ref` are the platform-owned representation of that reference. Agents, Tasks, Templates and portability packages should persist exact revision references when they need reproducible policy behavior rather than a mutable "latest" pointer.

The stable definition owns:

- canonical profile ID;
- owner and optional Project scope;
- current revision pointer;
- enabled/disabled lifecycle state;
- creation/update timestamps;
- schema version.

Each immutable revision owns:

- name and description;
- owner/Project scope snapshot;
- `RoutingRequirements` constraints;
- ordered canonical `ModelConfiguration` preference IDs;
- deterministic fallback policy;
- provenance;
- revision creation timestamp and schema version.

## Provider-neutral policy

`ModelRoutingProfilePolicy` stores only platform concepts. It may require context window, tool calling, structured output, streaming, modalities, reasoning metadata, local/self-hosted placement and an explicit canonical model configuration. `preferred_model_ids` adds an ordered list of canonical model configuration IDs.

It does **not** persist:

- provider-native model names;
- provider SDK/request objects;
- endpoint/base URLs;
- credentials or Secret material;
- provider health;
- runtime node health/location state;
- gateway-private routing/session state.

Those stay in their existing owners (`ModelRegistry`, provider adapters, SecretReference handling and runtime/node systems).

## Deterministic fallback semantics

`RoutingProfileFallbackPolicy.FAIL` means that when the configured explicit/ordered model preferences are unavailable or incompatible, routing fails canonically with `NO_COMPATIBLE_ROUTE`.

`RoutingProfileFallbackPolicy.ROUTE` means the router drops the unavailable explicit/preferred model choice and applies the profile's remaining capability/location requirements to the normal deterministic registry candidate set. Existing priority and canonical-ID tie breaking remain unchanged.

The router's `route_profile()` method consumes one exact `ModelRoutingProfileRevision`; it does not fetch or mutate profile persistence itself.

## Persistence

`JsonModelRoutingProfileRepository` is the dependency-free reference persistence implementation. It stores stable definitions and complete contiguous revision history, writes atomically and restores the same exact revision references after restart. Duplicate IDs, skipped/duplicate revisions and identity/scope mismatches fail canonically.

Disabling a profile changes lifecycle state on the stable definition; it does not rewrite historical revisions. `delete_profile()` exists as a repository compensation seam for rollback-safe portability import and is not the normal lifecycle operation.

`ModelRoutingProfileResolver` resolves an exact canonical revision, rejects mutable/non-canonical reference strings, requires the stable definition to be enabled and enforces Project scope before returning the immutable revision.

## Authorization and scope

`ModelRoutingProfileService` is the management boundary. When an Issue #15 `AuthorizationProvider` is supplied, create/version/read/enable/disable operations are authorized using `model-routing-profile:*` actions. Project-scoped profiles additionally require the same canonical `project_id` in `OperationContext`.

The repository itself does not make authorization decisions. This keeps storage reusable while ensuring application-facing lifecycle operations go through the existing authorization boundary.

## Runtime consumption

The standard single-node composition uses `DurableRoutingProfileAgentRuntime` and `DurableRoutingProfileConversationResponseProvider`. Both resolve the exact profile revision from the durable repository and then delegate selection to the existing `DeterministicModelRouter`.

The older `Mapping[str, RoutingRequirements]` seam in the base Issue #33/#72 runtime remains available only as a compatibility surface for existing embeddings and tests. It is not the source of truth in the standard deployment.

For an Agent with an exact `routing_profile_ref`, profile requirements are merged with Agent requirements and any allowed task-level override. The resulting effective constraints are applied to the same immutable profile revision, preserving that revision's ordered model preferences and fallback semantics.

## Portability through #79

Issue #79 consumes the #309 domain rather than defining another routing-policy resource.

`ModelRoutingProfilePortableSnapshot` carries the stable definition plus the complete immutable revision history. `ModelRoutingProfilePortableCodec`:

- uses resource type `model_routing_profile`;
- reports the optional Project scope and all referenced canonical model configurations as dependencies;
- preserves or remaps the stable profile ID according to the existing #79 import context;
- remaps Project and canonical model references deterministically;
- excludes provider-native identifiers, endpoint data, provider/node health, credentials and gateway-private state.

`ModelRoutingProfileImportMutationHandler` replays the complete history through the canonical repository. A failed partial replay is compensated by removing the just-created profile, and the normal #79 import executor can also roll the resource back if a later package resource fails.

The standard single-node portability workflow registers this codec and import handler whenever the routing-profile repository is available.

## Relationship to Templates

Existing Agent model policy contains `routing_profile_ref`; an exact `ModelRoutingProfileRef.canonical_ref` is the canonical value to place there when reproducibility is required. Templates can carry the same reference without becoming the profile source of truth.

The standard Template environment advertises enabled routing profiles as exact current-revision references. Issue #78 can therefore validate Template model-policy requirements against canonical #309 inventory rather than model configuration IDs or gateway-private policy names.

## Example

```python
profile = await service.create_profile(
    name="Local research",
    policy=ModelRoutingProfilePolicy(
        requirements=RoutingRequirements(
            min_context_window=32_000,
            tool_calling=True,
            local_only=True,
        ),
        preferred_model_ids=("model-research-large", "model-research-small"),
        fallback=RoutingProfileFallbackPolicy.ROUTE,
    ),
    owner_ref=owner,
    principal_ref=actor_id,
    context=operation_context,
)

route = DeterministicModelRouter(model_registry).route_profile(profile)
exact_ref = profile.ref.canonical_ref
```

Changing or replacing the provider behind `model-research-large` does not rewrite `exact_ref`: provider/runtime resolution remains behind `ModelRegistry`.
