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
    -> explicit import plan
    -> canonical repository/service mutation
```

No Python object representation, backend database row, provider session or host path is itself a portable contract.

The initial foundation in `ai_multi_agent_platform.portability` owns the package models, format schema, integrity verification, safety validation and serializer/deserializer registry. Resource-specific codecs and the mutation-oriented import planner are layered on top of these contracts rather than redefining the package format.

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

- `preserve` — import should preserve the canonical ID when the destination admits it;
- `regenerate` — import planning must allocate a destination ID and rewrite canonical references through an explicit mapping;
- `historical_preserve` — historical identity must remain stable and must not be silently activated as current runtime work.

The format carries the requested identity semantics. Conflict detection and the final mapping decision belong to import planning; codecs receive an `ImportContext` containing the accepted deterministic mapping.

## Dependencies

`DependencyRequirement` uses a stable kind plus identifier, optional version constraint and purpose. Initial dependency kinds are:

- canonical resource;
- plugin;
- capability;
- connector;
- model;
- secret reference.

Dependencies are descriptive requirements, not installation authority. Import must not auto-install untrusted code or silently create credentials merely because a package declares them.

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

The package checksum binds the complete manifest plus the sealed resources. Any modification to a payload, resource descriptor, dependency declaration, compatibility metadata, provenance metadata or exclusion report therefore invalidates integrity until the package is intentionally rebuilt.

Checksums provide corruption/tamper detection; they are not signatures and do not establish trust in the package author.

## Serializer/deserializer registry

`ResourceSerializerRegistry` contains explicit `ResourceCodec` implementations. There is no generic pickle/object fallback. Each codec owns mapping for one canonical resource type and must return JSON-safe platform semantics.

The registry:

- rejects duplicate resource-type registrations;
- rejects unknown resource types deterministically;
- safety-validates and seals serialized resources;
- verifies a resource before passing it to a deserializer;
- provides the accepted canonical ID mapping to the deserializer through `ImportContext`.

## Import transaction boundary

The foundation does **not** mutate canonical repositories. The next import layer must perform, before mutation:

1. schema/version/integrity verification;
2. platform and contract compatibility validation;
3. dependency availability checks;
4. authorization/security checks;
5. canonical ID conflict discovery;
6. deterministic preserve/regenerate mapping;
7. dry-run/preview reporting;
8. safe dependency-ordered import planning.

Only an accepted plan may enter a transactional/rollback-safe mutation boundary. A failed import must not leave partially imported canonical state.

## Portability versus backup

Portable export optimizes for canonical semantics and migration between compatible installations. It may deliberately omit runtime/provider material that a deployment backup would preserve.

Backup/restore (#40) instead protects one installation's operational state and may include backend-specific databases, indexes and deployment metadata. Neither mechanism is a substitute for the other.

## Foundation test coverage

`tests/test_portability.py` currently verifies:

- package serialize/deserialize round trip;
- resource and package checksum tamper detection;
- unsupported format-version rejection;
- plaintext-secret rejection;
- safe secret-reference placeholders;
- recursive runtime-private-state rejection;
- codec registry round trip and canonical ID remapping;
- duplicate/unknown codec failure.

Resource-specific Agent/Team, file/artifact, memory/knowledge, automation and historical Task codecs, dependency/conflict preview, rollback-safe import, and Control Plane/CLI surfaces remain follow-up work within #79.
