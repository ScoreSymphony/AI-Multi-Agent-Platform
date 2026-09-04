# Evaluation discovery in global Search

This document records the issue #45 Search integration for the canonical evaluation framework from issue #19.

## Canonical sources

Search consumes only the registered Control Plane resources supplied by the Evaluation domain:

- `evaluation-suites` -> `evaluation-suite` resources;
- `evaluation-runs` -> `evaluation-run` resources.

Search does not read `EvaluationHistoryRepository`, `EvaluationRunner` internals or evaluator/provider state directly. Evaluation remains authoritative for suites, runs, results, comparisons and reproducibility evidence.

## Evaluation suites

A searchable suite exposes only safe top-level discovery metadata already present on the canonical northbound resource:

- exact suite reference / Search resource ID;
- suite name;
- suite description;
- suite tags;
- `suite_id`;
- suite version.

The exact configured suite version is exposed as the Search result `version` and remains part of the canonical suite reference.

The suite's nested `cases` collection is deliberately **not traversed** by Search. Therefore case names, fixtures, input templates, assertions, metric rules, rubric criteria and other case-level material do not become global search text merely because the parent suite is discoverable.

## Evaluation runs

A searchable run exposes only safe top-level run metadata:

- canonical evaluation run ID;
- `suite_id`;
- `suite_version`;
- run status;
- optional `baseline_run_id`;
- start/completion time for generic Search time filtering.

The display title is derived as `Evaluation run for <suite_id> <suite_version>` when both fields are available.

Evaluation runs do not have a generic mutable `updated_at` field. For Search's existing `updated_after` / `updated_before` discovery filters, the derived Search document uses `completed_at` when present and otherwise `started_at`. This is discovery metadata only and must not be interpreted as a new canonical Evaluation revision timestamp.

## Explicit privacy boundary

Search must not recursively index nested Evaluation evidence or configuration. In particular, the following remain outside global search text:

- `snapshot.environment` keys or values;
- configuration snapshot references, commits or provider/runtime details unless separately exposed as an explicitly approved top-level discovery field;
- nested Evaluation results;
- evaluator descriptors;
- assertions and actual/expected values;
- metric values and thresholds;
- rubric findings;
- comparison/regression findings;
- case fixtures and case input templates.

Single-resource Evaluation reads may expose richer canonical detail to an authorized caller. That does not imply that the same detail belongs in a global discovery index.

## Authorization and non-disclosure

Evaluation Search uses the existing registered-resource Search path. Candidate results are authorized with the same collection vocabulary as direct Evaluation discovery:

- `evaluation-suite:list` for `evaluation-suite` results;
- `evaluation-run:list` for `evaluation-run` results.

Authorization is applied before result totals and exact-ID output become caller-visible. A denied suite or run therefore does not leak its existence through counts, exact-ID lookup or snippets.

Search remains a derived convenience layer. Authorization of a Search hit does not grant any additional permission to read, execute, compare or mutate Evaluation resources.

## Canonical navigation

Registered collection identity supplies canonical references:

- `/api/v1/evaluation-suites/{suite-ref}`;
- `/api/v1/evaluation-runs/{run-id}`.

Clients should follow those canonical Control Plane resources rather than treating Search documents as Evaluation state.

## Rebuild and synchronization

Evaluation resources participate in the normal #45 registered-domain rebuild path. The Search checkpoint/stale-recovery contract remains applicable without giving Search ownership of Evaluation history.

A future durable/event-driven Search provider may incrementally project Evaluation changes, but a detected synchronization gap must use the normal stale/rebuild recovery path. No external Search backend, vector database or paid service is required for Evaluation discovery.
