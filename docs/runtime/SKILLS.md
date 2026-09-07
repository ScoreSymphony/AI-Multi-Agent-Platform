# Canonical Skills and reproducible Skill Bundles

Issue #588 introduces the platform-owned Skill layer for reusable task methods.
A Skill answers **how** work should be performed. It is deliberately separate from:

- Agents/Agent Teams (#33), which own execution identity and profile policy;
- Capabilities/Tools (#12), which own executable operations and invocation policy;
- authorization/Approval (#15), which owns permissions and sensitive-action approval;
- Plugins (#20), which package/integrate implementations;
- Verification (#86), which decides whether work satisfies completion policy.

A Skill never grants itself a Capability, permission, Approval, secret, or verification result.
Provider/orchestrator formats are presentation adapters only.

## Canonical resources

`SkillDefinition` is the stable identity and points to the latest immutable `SkillRevision`.
A revision carries method content or a versioned content reference plus provider-neutral metadata:

- semantic purpose categories;
- exact Skill dependencies;
- Capability requirements and compatibility constraints;
- compatible Agent roles;
- model/runtime requirements through canonical model-routing abstractions;
- expected inputs/outputs;
- workspace assumptions and side effects;
- explicit composition conflicts;
- risk, trust and evaluation state;
- source/revision/license/checksum/signature metadata for external Skills;
- project/workspace and canonical owner scope;
- enable/disable and deprecation/replacement state.

Historical revisions are immutable. Updating a Skill appends exactly one revision and does not mutate
older revisions.

## Resolver precedence and minimality

`SkillResolver` resolves an exact bundle in deterministic order:

1. Agent-required Skills;
2. Task-explicit Skills;
3. Planner-selected Skills;
4. Agent-default/recommended Skills.

Exact Skill dependencies are expanded depth-first before the dependent Skill. Installed Skills are
never added merely because they are visible in the registry.

Resolution fails closed when any selected/dependent revision is missing, disabled, disallowed by
scope, untrusted, incompatible with the Agent role, deprecated without an explicit compatibility
allowance, in a dependency cycle, in conflict with another selected Skill, or requires an unavailable
Capability/model property.

Capability requirements are checked against the server-resolved Agent/Run capability scope. A Skill
cannot widen that scope. When a canonical `CapabilityRegistry` is attached, exact/compatible versions,
permissions and worker requirements are resolved through #12. Model requirements are checked against
a server-selected canonical `ModelConfiguration`; provider-native model names remain outside the Skill
schema.

## Reproducible Skill Bundle evidence

Before execution, the resolver materializes an immutable `SkillBundle` containing:

- a canonical `skill_bundle_id`;
- deterministic SHA-256 digest;
- exact ordered Skill IDs/revisions and content digests;
- source revision/checksum and trust evidence;
- resolver/policy versions;
- Task/Step/Run/Agent revision context;
- resolved Capability IDs/versions;
- model identity/revision in audit metadata when applicable;
- creation/audit metadata.

The random bundle ID and creation timestamp are not inputs to the digest. Identical canonical inputs
therefore produce the same digest, while effective content or execution requirements change it.

`SkillRunBinding` pins one bundle ID/hash to the exact Run, Task and Agent revision. The binding may
then be pinned once to a canonical `AgentRunRecord`. A different bundle cannot later claim the same
AgentRun, and an existing binding cannot be rebound to another AgentRun.

`JsonSkillRepository` persists all Skill histories, bundles and bindings using a versioned JSON schema.
On restart, histories are reconstructed through the same repository invariants, preserving historical
Run meaning even after active Skills receive later revisions.

## Adapter boundary

`SkillBundleRenderer` is replaceable. The reference and Markdown renderers demonstrate that one
canonical bundle can be represented differently for different harnesses.

A renderer may change text/metadata representation but must return the original bundle ID and digest.
`SkillExecutionCoordinator` rejects a renderer that changes either identity field. Adapters do not
receive authority to add Capabilities or permissions.

## Third-party trust lifecycle

External Skills enter the registry disabled and follow the explicit lifecycle:

`discovered -> source_verified -> security_reviewed -> pilot -> adopted`

At any applicable review stage they may be rejected or deferred. Adoption requires a passed evaluation.
`evaluation_metadata` records deployment-specific evidence such as evaluation suite/revision, reviewer,
metrics or policy references.

External source metadata records the exact source/revision/license and, where available, checksums or
signatures plus requested Capabilities, filesystem/network implications, embedded hooks/MCP references
and cost implications.

A third-party Skill cannot enable itself. Enabling is separately rejected until the Skill is adopted and
its evaluation has passed.

Portable imports do not silently transfer third-party runtime trust between deployments. The Skill
history remains attributable to its source, but imported external revisions are restored disabled,
`discovered` and `not_evaluated`, with the source trust/evaluation/enable state recorded in import
provenance for target-side revalidation.

## Control Plane

`register_skill_control_plane(...)` adds these read collections:

- `skills`;
- `skill-bundles`;
- `skill-bindings`.

Lifecycle commands:

- `skill.create`;
- `skill.update`;
- `skill.clone`;
- `skill.enable`;
- `skill.disable`;
- `skill.deprecate`;
- `skill.trust`.

When an Agent service and `SkillExecutionCoordinator` are composed, runtime commands are also exposed:

- `skill.resolve`;
- `skill.bind-agent-run`.

Capability/permission/worker/model availability is server-resolved through
`SkillExecutionEnvironmentResolver`. Clients may request Skill refs, but they may not inject trusted
runtime authorization fields.

## Portability

`SkillPortableCodec` exports a complete, contiguous Skill revision history with exact source/provenance
and dependencies. `SkillBundlePortableCodec` exports immutable historical bundle evidence and uses
historical-preserve identity semantics.

`SkillImportMutationHandler` and `SkillBundleImportMutationHandler` provide rollback-capable import
mutation boundaries. Historical bundle IDs/hashes and their Run/Task/Agent/Skill references cannot be
remapped because doing so would change the meaning of the evidence.

## Baseline operation

The reference Skill Registry, resolver, JSON repository, renderers and portability codecs require no
hosted Skill service and no paid external AI/API dependency.
