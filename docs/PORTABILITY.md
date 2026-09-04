# Portable canonical import/export

Issue #79 defines a portability boundary for moving canonical platform resources between compatible installations. Portability is intentionally distinct from backup/restore: a portable package contains canonical configuration, selected durable content and explicit dependency metadata, but excludes deployment-private runtime state and secret material.

## Architecture boundary

The canonical direction is:

```text
canonical resource
    -> explicit ResourceCodec
    -> PortableResource
    -> versioned PortablePackage manifest
    -> schema + checksum verification
    -> dependency/conflict preview
    -> accepted ImportContext mapping
    -> resource-specific non-mutating preflight
    -> dependency-ordered mutation
    -> reverse compensation on failure
```

No Python object representation, backend database row, provider session or host path is itself a portable contract.

`ai_multi_agent_platform.portability` owns package models, format schema, integrity verification, safety validation, explicit codecs, import preview and the rollback-safe import execution boundary. Resource-specific codecs and mutation handlers layer on those contracts rather than redefining package semantics.

## Portable package format v1

`PORTABLE_FORMAT_VERSION = "1.0"` identifies the first public package contract. A package contains:

- source platform version and creation timestamp;
- an inventory of exported resource type/ID/version tuples;
- a payload and SHA-256 checksum for each resource;
- explicit per-resource ID policy;
- resource and package dependency requirements;
- required plugin/capability/connector/model/secret-reference identifiers;
- provenance and compatibility metadata;
- an explicit report of intentionally excluded/non-portable state;
- a package-level SHA-256 checksum binding manifest and resources.

The runtime JSON Schema is `PORTABLE_PACKAGE_SCHEMA_V1` in `portability/schema.py`. Deserialization validates the document before constructing package objects, then verifies each resource checksum, descriptor binding and the package checksum.

## Canonical identity policies

Each exported resource declares one `IdPolicy`:

- `preserve` — import preserves the canonical ID when the destination admits it;
- `regenerate` — import planning allocates a destination ID and codecs rewrite canonical references through the accepted mapping;
- `historical_preserve` — historical identity remains stable and must not be silently activated as current runtime work.

Conflict detection and the final mapping decision belong to import planning. Codecs receive an `ImportContext` containing the accepted deterministic mapping.

## Dependencies

`DependencyRequirement` uses a stable kind plus identifier, optional version constraint and purpose. Dependency kinds include:

- canonical resource;
- plugin;
- capability;
- connector;
- model;
- secret reference.

Dependencies are descriptive requirements, not installation authority. Import does not auto-install untrusted code or silently create credentials merely because a package declares them.

Canonical resource dependencies also drive topological import ordering. Missing required dependencies and dependency cycles are reported by the dry-run before mutation.

## Secret boundary

Portable payloads must not contain plaintext passwords, API keys, bearer tokens, private keys, cookies or equivalent credential material. The portability validator reuses the platform security redaction boundary instead of maintaining a competing secret vocabulary.

Canonical `SecretReference` placeholders are allowed because they identify a required secret without carrying the secret value. Destination-side resolution and authorization remain normal #15/#34 security operations.

## Runtime-state boundary

Portable canonical resources must not carry backend-private execution state such as:

- Hermes private session IDs;
- Forge private job state/IDs;
- live worker leases or reservations;
- active trace/span identifiers;
- local process IDs;
- temporary cache/materialization paths;
- provider access tokens;
- rebuildable backend search/vector IDs;
- filesystem/object-store implementation paths.

Known private fields are rejected recursively during sealing and verification. Exporters should also record intentional omissions in `ExcludedState` so users can distinguish deliberate non-portability from accidental data loss.

Backend/external identifiers that are legitimate durable provenance may still be represented by resource-specific codecs under the platform's existing namespaced external-reference conventions. They must never replace canonical IDs.

## Integrity semantics

Resource checksum input is deterministic canonical JSON over:

- resource type;
- canonical resource ID;
- resource version;
- ID policy;
- dependency declarations;
- portable payload.

The package checksum binds the complete manifest plus sealed resources. Any modification to a payload, resource descriptor, dependency declaration, compatibility metadata, provenance metadata or exclusion report therefore invalidates integrity until the package is intentionally rebuilt.

Checksums provide corruption/tamper detection; they are not signatures and do not establish trust in the package author.

Durable files add an independent integrity layer: decoded file bytes must match the canonical `FileRecord.sha256` and size before import, and the destination `FileProvider` is checked again after materialization.

## Serializer/deserializer registry

`ResourceSerializerRegistry` contains explicit `ResourceCodec` implementations. There is no generic pickle/object fallback. Each codec owns mapping for one canonical resource type and must return JSON-safe platform semantics.

The registry:

- rejects duplicate resource-type registrations;
- rejects unknown resource types deterministically;
- safety-validates and seals serialized resources;
- verifies a resource before passing it to a deserializer;
- provides the accepted canonical ID mapping to the deserializer through `ImportContext`.

A codec only grants serialization/deserialization capability. It never grants destination write authority. Mutations require a separately registered `ImportMutationHandler`.

## Agent and Agent Team semantics

Agent and Agent Team resources carry complete immutable revision history, not active runtime/session state.

Agent dependencies describe canonical model assignments, capabilities, project/workspace scope and declared knowledge/memory configuration references. Team dependencies describe member Agent revisions and shared scope/configuration references. Clone/import-as-new uses deterministic ID mapping, including Team member/leader/delegation references back to imported Agents.

Import reconstructs revision history through the normal Agent repository boundary. Agent/Team mutation handlers self-compensate if a multi-revision write fails part-way through.

## File and Artifact semantics

Portable `file` resources contain canonical `FileRecord` metadata plus Base64 transport bytes. They never use filesystem paths or object-store keys as identity.

Portable `artifact` resources preserve canonical metadata and external references but deliberately omit `Artifact.uri`, because that field may be a source filesystem path, provider-native object location or temporary URL. The target installation owns its new materialized locator.

File import:

1. verifies decoded bytes against canonical size and SHA-256;
2. remaps canonical File/Artifact/Project references;
3. creates bytes through the destination `FileProvider`;
4. verifies destination checksum and size;
5. restores Artifact links using remapped canonical IDs;
6. compensates a partially materialized File if a later per-file step fails.

Package-level compensation is handled by `ImportExecutor`.

## Scoped Memory semantics

Memory portability is deliberately more restrictive than generic JSON transport. The portable contract preserves the canonical `MemoryEntry` value, retention metadata, classification, metadata and provenance while carrying the export-time project context needed to enforce privacy for project-bound scopes.

### Allowed scopes

The ordinary portable format allows durable:

- `task` Memory;
- `agent` Memory;
- `workspace` Memory;
- `user` Memory;
- `historical` Memory.

`short_term` Memory is never portable. It represents active execution/session context and is part of the runtime-state exclusion boundary.

Expired Memory is not exported by the portable snapshot contract.

### Project privacy

Task-, Agent- and Workspace-scoped Memory snapshots record the authorized export `project_id`. This is privacy context, not a backend storage identity.

On import:

- Task scope IDs are remapped through canonical Task mappings;
- Agent scope IDs are remapped through canonical Agent mappings;
- Workspace scope IDs are remapped through canonical Project mappings;
- the target project must match the remapped project for scopes whose canonical access policy denies cross-project access;
- `explicit_policy_only` cross-project semantics require an explicit `MemoryImportPrivacyPolicy` grant rather than an implicit migration shortcut.

The destination `MemoryProvider` remains authoritative. Using `AuthorizedDataMemoryProvider` therefore retains the normal #15 authorization gate during both export reads and import writes.

### User/owner privacy

Ordinary import does not silently transfer `MemoryEntry.owner_ref`. By default the destination actor must match the preserved owner. `MemoryImportPrivacyPolicy.allow_owner_transfer` is an explicit exception for callers that already obtained appropriate authorization; it does not bypass provider authorization.

User-scoped Memory additionally remains bound to its canonical user scope. A package imported by another user cannot silently rewrite the user scope.

### Provenance and Memory chains

Memory provenance is serialized explicitly and preserved. `supersedes_memory_id` and `superseded_by_memory_id` are canonical Memory references: they are declared as dependencies and deterministically remapped when their referenced Memory resources are imported together.

No backend search/vector/index identity is carried by the Memory resource.

## Historical Task and Run semantics

Portable `task_history` resources are archival records, not executable Task imports. They always use `historical_preserve` identity semantics.

A Task history snapshot is accepted only when the canonical Task is terminal and every referenced Run is terminal. Draft, ready, running and waiting Tasks are rejected, as is a terminal Task that still references a non-terminal Run.

The historical snapshot preserves terminal Task/Run projections, revisions, output metadata, lifecycle event order, timestamps, provenance, durable external references and canonical Plan/Step/Artifact/Result relationships. Runtime execution authority is removed: backend execution references, worker IDs, active trace/span IDs, live leases/reservations, recovery state and equivalent provider-private fields are not portable history.

Historical import writes only through `HistoricalTaskArchiveRepository`. It never commits imported lifecycle events to the live `EventRepository`, so imported history cannot become schedulable, recoverable or dispatchable work. `TaskHistoryImportMutationHandler` participates in the ordinary package rollback model by deleting the archive entry if a later package mutation fails.

The focused contract and invariants are documented in `PORTABILITY_TASK_HISTORY.md`.

## Dry-run and conflict boundary

`ImportPreviewService` is mutation-free. Before a package may enter the executor it reports or computes:

- package/schema/integrity validity;
- existing canonical ID conflicts;
- caller-supplied name conflicts;
- required and optional missing dependencies;
- deterministic preserve/regenerate target IDs;
- canonical reference mapping;
- dependency-safe topological order;
- dependency cycles.

The preview checksum binds it to one exact package. `ImportExecutor` rejects a stale preview, a non-ready preview, incomplete mappings or an import order that does not cover the package exactly once.

## Import transaction and recovery boundary

The platform intentionally does not pretend a single database transaction can atomically cover replaceable Agent repositories, File providers, Memory providers and future external adapters.

The baseline multi-provider transaction model is therefore:

1. verify package and accepted preview;
2. resolve every codec and mutation handler;
3. deserialize every resource;
4. execute every resource-specific non-mutating preflight;
5. apply resources in dependency order;
6. on failure, compensate already-applied resources in reverse order;
7. report explicitly if compensation itself is incomplete.

A mutation handler that fails after partially changing its own resource must compensate that partial mutation before raising. The package executor then rolls back only resources whose `apply()` completed successfully.

Rollback error details pass through the platform redaction boundary before entering an import failure report.

## Portability versus backup

Portable export optimizes for canonical semantics and migration between compatible installations. It deliberately omits runtime/provider material that a deployment backup may preserve.

Backup/restore (#40) instead protects one installation's operational state and may include backend-specific databases, indexes and deployment metadata. Neither mechanism is a substitute for the other.

## Current test coverage

The #79 portability stack verifies at least:

- package serialize/deserialize round trip;
- resource and package checksum tamper detection;
- unsupported format-version rejection;
- plaintext-secret rejection;
- safe secret-reference placeholders;
- recursive runtime-private-state rejection;
- codec registry round trip and canonical ID remapping;
- duplicate/unknown codec failure;
- Agent/Team full-revision round trip;
- composite Agent/Team reference remapping;
- dependency/conflict preview;
- provider-neutral File/Artifact round trip and byte checksum verification;
- destination File compensation;
- package-wide Agent/Team/File reverse rollback;
- stale/not-ready preview rejection before mutation;
- durable Workspace Memory round trip with provenance preservation;
- deterministic Workspace Project remapping;
- cross-project Workspace Memory rejection before mutation;
- cross-user User Memory rejection;
- `short_term` Memory runtime-state exclusion;
- terminal Task/Run history snapshot from canonical event streams;
- rejection of active Tasks and non-terminal Runs from historical export;
- historical Task archive import without creation of a live kernel stream.

Knowledge-source/config metadata, further canonical configuration codecs, import/export reports and Control Plane/CLI surfaces remain follow-up work within #79.
