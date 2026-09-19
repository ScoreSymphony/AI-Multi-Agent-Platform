# Single-node transient-state reconciliation (#1153)

## Purpose

Ordinary single-node restart must restore canonical durable ownership without reviving
process-local authority from the previous Control Plane process. This document records the
owner matrix used by startup reconciliation. It complements, rather than replaces, the
canonical lifecycle owners from #707, #14, #36 and #37.

## Owner matrix

| State class | Durable authority | Restart rule | Cleanup / recovery action | Fail-closed case | Evidence |
| --- | --- | --- | --- | --- | --- |
| Worker reservations, leases and claims | #14 distributed registry / dispatch records | Reconcile before canonical Run recovery | DistributedRuntime.reconcile() expires or reclassifies stale ownership | LOST/CANCEL_PENDING ownership for an active canonical Run blocks startup | ordinary startup report distributed_jobs_reconciled plus unresolved Run evidence |
| Local execution handles | canonical Task/Run + lifecycle backend | process-local handle is never authoritative | #707 asks the backend for current execution truth | active Run with no provable backend ownership remains ORPHANED_RECONCILIATION_REQUIRED | ordinary startup report task entries |
| Terminal handles and attachments | none; terminal adapter handles are process-local | never restore a handle from the previous process | a new TerminalSessionService starts with empty handle/session/attachment maps | any future durable terminal implementation must add an explicit owner reconciler before restoration | terminal lifecycle tests; no durable handle store is read at startup |
| Reference browser/tool sessions | none; reference browser sessions are process-local and bounded by TTL | never rehydrate old browser handles | new provider instance starts with no old session handles | provider-specific durable sessions require an explicit adapter-owned recovery contract | browser session lifecycle tests |
| Workspace materialization locks / temp trees | #37 Workspace metadata + #1155 persistence/filesystem owner | canonical Workspace metadata wins; temp materialization is not authority | reuse #1155 crash-interrupted materialization cleanup | unknown temp ownership is retained and surfaced rather than guessed | #1155 persistence/readiness evidence; #1153 does not create a second cleanup path |
| Authentication browser sessions | #36 SQLite authentication store | revoked_at and expires_at survive restart and remain authoritative | no authority is revived; expired/revoked rows may remain as durable audit history | malformed/unreadable persistence is a persistence-health/startup blocker | single-node-transient-state extension session counts |
| Plan/Step retry and backoff | durable coordinator record (retry_due_at, retry state) | timer identity is derived from durable state, not an old process timer | #707 calls DurablePlanStepCoordinator.reconcile_all() before kernel recovery | inconsistent coordinator state remains canonical reconciliation work | ordinary startup report plans_reconciled |
| Automation delivery / retry ownership | durable Automation + TriggerDelivery + runtime cursor state | stale PENDING/PROCESSING must be resolved before Automation runtime starts | enabled owner: replay through the existing stable TaskCreator idempotency key; inactive PENDING: settle REJECTED; durable retry deadlines remain intact | missing owner, inactive uncertain PROCESSING, or still-nonterminal replay blocks readiness | single-node-transient-state extension evidence and Automation audit events |
| Automation dispatch/event cursors | SQLite Automation runtime state | processed-event and command replay state is durable | runtime resumes from durable event cursor and command idempotency records | persistence failure blocks through canonical persistence/startup health | Automation runtime SQLite state and audit |
| External/provider side-effect references | #1154 external-effect recovery owner | no blind re-execution from an uncertain pre-crash side-effect window | compose the #1154 startup extension | uncertain external outcome blocks or requires explicit provider-neutral resolution | #1154 external-effect recovery evidence |
| Temp files/directories with missing canonical owner | #1155 persistence/filesystem owner | canonical metadata decides whether cleanup is safe | reuse #1155 bounded cleanup | unknown ownership is preserved and surfaced | #1155 diagnostics/readiness |

## Automation reconciliation semantics

The canonical Automation service already used a stable TaskCreator idempotency key:

automation:{automation_id}:{delivery.dedupe_key}

Before #1153, a duplicate trigger could opportunistically recover a durable nonterminal
delivery, but startup did not scan all such deliveries. Manual/webhook deliveries could therefore
remain stranded indefinitely when the process disappeared after persisting PENDING or PROCESSING.

The startup pass now scans those durable candidates before readiness:

1. A missing Automation owner is not guessed. The delivery is left unchanged and startup blocks.
2. An inactive owner with PENDING is safe to settle as REJECTED: the durable state proves Task
   creation had not entered the processing window.
3. An inactive owner with PROCESSING is uncertain. Startup does not execute new work and blocks
   for explicit resolution.
4. An enabled owner with PENDING or PROCESSING is replayed through the existing recovery path.
   PROCESSING is rewound only for attempt accounting, then uses the same TaskCreator idempotency
   key, so a Task already admitted before the crash is returned rather than duplicated.
5. Once a candidate settles to a terminal/retry-managed delivery state, the next startup scan is
   a no-op for that delivery.

The Automation runtime is started only after the ordinary startup recovery gate. A blocker from
this extension therefore keeps stale delivery ownership from becoming live runtime authority.

## Dependency boundaries

#1153 intentionally does not duplicate the work currently owned by #1152, #1154 or #1155.
Final integrated acceptance must be run after those slices are on the target branch so the startup
gate exercises graceful-shutdown leftovers, external-effect uncertainty, Workspace materialization
cleanup and persistence/filesystem recovery together.
