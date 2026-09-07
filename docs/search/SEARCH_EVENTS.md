# Canonical Event Discovery in Global Search

This document records the Issue #45 integration of canonical lifecycle Events into the platform-wide Search layer.

The general invariants in `docs/SEARCH.md` continue to apply: Search is derived and non-authoritative, canonical IDs remain primary identity, the Search provider is replaceable, and authorization happens before caller-visible totals, snippets, cursors or exact-ID results are calculated.

## Searchable Event scope

Global Search indexes canonical domain Events from the platform EventRepository while rebuilding the already-known canonical Task streams.

The Event source is the same canonical event history used by the Task timeline. Search does not create a parallel event store and does not derive canonical history from logs, telemetry, Hermes, Forge or another adapter.

Derived observability timeline entries remain distinct from canonical Events and are not indexed as `event` resources by this integration.

## Canonical Event identity

Each Search document keeps:

- canonical Event ID as `resource_id`;
- resource type `event`;
- canonical Event type (`event_type`);
- canonical subject type and subject ID;
- parent Task owner and Project scope;
- Event occurrence time;
- provenance indicating the canonical EventRepository.

There is no invented `/api/v1/events/{id}` read API. A Search result navigates to the canonical owning Task timeline:

```text
/api/v1/tasks/{task_id}/timeline
```

That timeline remains the northbound inspection surface for the Event in context.

## Time semantics

Canonical Events are immutable lifecycle records and have `occurred_at`, not a mutable `updated_at` lifecycle.

For the generic Search time filter contract, the derived Event SearchDocument maps:

```text
Event.occurred_at -> SearchDocument.updated_at
```

Therefore `updated_after` / `updated_before` on `type=event` mean an inclusive Event occurrence-time window. This mapping is a Search projection only; it does not imply that canonical Events can be updated.

## Safe indexed metadata

Event keyword discovery is intentionally limited to safe structural metadata:

- canonical Event ID;
- `event_type`;
- `subject_type`;
- `subject_id`;
- parent Task/Project/owner scope supplied by the canonical Task stream.

Display labels are derived from those fields, for example:

```text
task.created for task task_<canonical-id>
```

## Event payload exclusion

The canonical Event `payload` is deliberately not promoted into global Search text or snippets.

Event payloads may contain lifecycle details, user-provided Task text or other domain data that is appropriate on the authorized timeline but inappropriate for broad cross-resource full-text indexing.

Knowing a value that occurs only inside an Event payload must therefore not make that Event discoverable through Global Search.

Search also does not flatten arbitrary correlation, causation, trace or adapter/provider structures into global Event text merely because those fields may exist in the richer canonical Event resource.

## Authorization and non-disclosure

Every Event candidate is associated internally with the canonical Task stream from which it was rebuilt.

Before returning an Event result, Search reuses the existing Task timeline authorization action:

```text
event:list
```

The authorization request receives:

- parent Task ID as the resource reference;
- parent Task owner type and owner ID;
- parent Project ID.

Unauthorized Events are removed before:

- result totals;
- snippets;
- cursors;
- exact Event ID results;
- Event subject searches;
- Project-filter results.

A caller who knows or guesses an Event ID, hidden Task ID or hidden Project ID must not learn that the Event exists through Search.

## Rebuild semantics

The correctness-first Search rebuild already iterates canonical Tasks. During that same pass it reads the canonical Event stream for each Task and derives Event SearchDocuments.

This preserves the existing rebuild guarantee:

- Search remains reconstructable from canonical sources;
- no event-specific search persistence becomes authoritative;
- canonical Event history remains owned by the EventRepository/kernel contract;
- Search provider replacement does not change Event identity or lifecycle semantics.

## Tests

The Issue #45 Event integration proves:

- discovery by canonical Event type;
- exact canonical Event ID lookup;
- canonical subject discovery;
- Project filtering;
- inclusive occurrence-time filtering through the generic Search time contract;
- canonical Task timeline navigation;
- real `task.created` payload text remains non-searchable and absent from Search serialization;
- hidden Task Events do not leak through common Event-type queries, exact IDs, subject IDs, Project filters or counts;
- derived observability telemetry is not misrepresented as canonical Event Search results.

## Baseline cost and provider constraints

Event discovery uses the existing local/replaceable SearchProvider boundary and canonical EventRepository. It requires no external search service, embeddings, vector database or paid API.
