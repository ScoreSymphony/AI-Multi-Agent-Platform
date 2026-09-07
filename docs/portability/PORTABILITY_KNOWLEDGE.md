# Portable KnowledgeSource semantics

Issue #79 treats canonical knowledge content and provider-owned retrieval indexes as different portability layers.

## Portable resource

The portable resource type is `knowledge_source`.

A portable KnowledgeSource snapshot contains:

- canonical source identity, title, owner/creator, project scope and revision;
- source metadata and source-side lifecycle timestamps;
- the canonical content checksum;
- the current text needed to rebuild retrieval on the destination when content exists;
- the source document ID and timestamp as source provenance;
- a provider-neutral declaration that the destination index must be rebuilt.

The source Project is a canonical resource dependency and is remapped through `ImportContext`.

## Rebuildable index boundary

`IndexReference.index_id` is deliberately not portable identity. Neither vector IDs, search backend IDs, embedding row IDs, provider collections nor local index paths belong in the portable payload.

Exporters should record `knowledge_index_exclusion(source_id)` in the package manifest. It emits an `ExcludedState` with category `rebuildable_index` so operators can distinguish intentional index omission from missing data.

The destination provider creates a new index for the imported source revision. A successful import requires that the provider reports the rebuilt index as `ready` for that exact revision.

## Current source content

The current `KnowledgeDocument` text is transport material used to reconstruct destination retrieval. Its checksum is verified before packaging and again against the document returned by destination ingestion.

The source `KnowledgeDocument.document_id` is retained only as provenance in the portable source payload. `KnowledgeProvider.ingest_source()` owns the new destination document identity. Source document IDs therefore never replace destination canonical IDs and are not used by Agent/Team source references; those references target the canonical `KnowledgeSource.source_id`.

Portable locations may be durable citation locations such as `notes/chapter.md` or an HTTPS source locator. Absolute filesystem paths and `file://` locations are rejected because they are installation-private storage state.

## Source lifecycle state

Portability preserves source metadata but rebuilds operational retrieval state:

- `registered` sources without current content may be moved as configuration-only sources;
- `ready` sources require current content in the portable snapshot;
- `failed` source configuration may be moved and retried on the destination;
- `indexing` is active provider runtime state and is rejected;
- `removed` sources are not portable active resources.

During import the destination source starts at `registered`; if portable content exists, the destination provider ingests that content and must reach `ready` for the source revision.

## Privacy and authorization

Knowledge import uses the ordinary `KnowledgeProvider` boundary. An `AuthorizedDataKnowledgeProvider` therefore remains the authoritative authorization gate when supplied to the import handler.

`KnowledgeSourceImportMutationHandler` additionally enforces conservative portability rules before mutation:

- a remapped Project-scoped source must match the destination `DataAccessContext.project_id`;
- source ownership is preserved by default;
- implicit owner transfer is rejected;
- `KnowledgeImportPrivacyPolicy.allow_owner_transfer` is an explicit caller-side exception and does not bypass provider authorization.

## Rollback

The mutation handler registers the source, ingests portable content and verifies the rebuilt index. If ingestion or verification fails after registration, the handler compensates itself through `KnowledgeProvider.remove_source()` before returning the error.

If a later package resource fails, the package-wide `ImportExecutor` invokes the same removal operation during reverse compensation.

Providers may retain their normal tombstone/audit representation after `remove_source()`. The portability invariant is that the failed imported source is no longer an active/readable/searchable canonical source through the public provider contract. This is the same logical-delete rollback model already used by portable File resources.

## Non-goals

This contract does not serialize embedding vectors, keyword tables, provider-native indexes, object-store locations or external connector credentials. Those are destination-owned or dependency-managed state and must be rebuilt/resolved separately.
