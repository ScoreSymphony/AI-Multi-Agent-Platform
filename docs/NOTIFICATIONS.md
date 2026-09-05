# Notifications and user attention

Issue #75 defines the platform-owned notification and user-attention subsystem.

Notifications are **projections over canonical source state**. They are not the source of truth
for Tasks, Runs, Approvals, Verifications, Automations, memberships, accounting/resource state,
workers/nodes, connectors, or security state. A notification may point at one of those resources,
but lifecycle changes must still be executed through the owning canonical subsystem and must be
re-authorized there.

## Canonical model

`Notification` stores platform-owned attention state:

- canonical `notification_*` ID;
- category and severity;
- title plus structured, redacted summary;
- canonical recipient (`user`, `team`, or `organization`);
- canonical source resource type/ID;
- optional project/workspace and Task/Run/Approval/Verification/Node/Automation/Membership refs;
- optional source navigation/command actions;
- unread/read/acknowledged/dismissed/archived lifecycle;
- deterministic aggregation key and occurrence count;
- timestamps, optional expiry, correlation/causation IDs;
- namespaced delivery metadata and separate delivery-attempt records.

The notification lifecycle only describes attention state. For example, acknowledging an
`Approval required` notification does not approve the Approval. Calling `mark-read` on an already
acknowledged notification also preserves `ACKNOWLEDGED`; read actions never downgrade stronger
attention state.

## Persistence and runtime recovery

The domain is defined by repository/state protocols. The reference implementation includes:

- in-memory repositories/state for tests and explicitly ephemeral embeddings;
- `SqliteNotificationRepository` and `SqliteNotificationPreferenceRepository`;
- `SqliteDeliveryAttemptRepository` for external-delivery history and retry/dedupe identity;
- `SqliteNotificationRuntimeState` for the processed canonical-event cursor.

The production-shaped single-node composition binds these stores to `notifications.sqlite3`.
Notification records, preferences, delivery attempts and the event-projection cursor therefore
survive process restart. Re-reading the canonical Event repository after restart does not create a
second terminal Task notification.

SQLite is a reference implementation choice, not a canonical requirement. The repository and
runtime-state contracts remain replaceable.

Notification repositories also expose canonical enumeration for internal rebuildable projections.
This is not a public list-all endpoint and does not bypass recipient authorization; it exists so a
derived Search index can be reconstructed after restart without inventing a privileged synthetic
user.

## Recipient identity and current authorization

Recipients are canonical platform identities. Provider-native account IDs, e-mail addresses, or
connector-specific identities are never canonical notification recipients.

`RecipientRef` requires the existing canonical ID vocabulary (`user_*`, `team_*`,
`organization_*`). Control Plane inbox access derives the recipient from authenticated canonical
owner context. Cross-recipient reads use not-found semantics so inaccessible notification
existence is not leaked.

Two separate checks intentionally exist:

1. `RecipientEligibilityGuard` is evaluated before **new** attention is created. A #15/#87-aware
   deployment can stop new delivery to a removed, suspended or otherwise ineligible recipient.
2. The public Notification Control Plane rechecks the **current source-resource authorization** on
   every list/get/count/mutation request using the actual authenticated `RequestContext`. The
   principal, actor type, owner scope, project scope and credential ceiling are therefore not
   replaced by the stored notification recipient.

This second check is important for historical attention: revoking access to a Task, Approval,
Automation, Connector or other source immediately hides its existing notification from inbox
lists and unread counts. Direct reads/actions return not-found semantics, and `mark-all-read` does
not mutate hidden records.

Notification actions and source links are navigation/command metadata only. The owning Control
Plane subsystem authorizes the eventual source read or command again.

## Preferences and quiet hours

Per-recipient preferences support:

- enabled categories;
- minimum severity;
- optional project filtering;
- mute state;
- in-app enable/disable;
- configured external channel IDs;
- duplicate aggregation preference;
- deadline-reminder enable/disable;
- configurable deadline lead time;
- overdue-reminder enable/disable;
- optional quiet-hours start/end plus IANA timezone.

Quiet hours suppress configured **external** delivery while preserving the canonical in-app
attention item. Reminder preferences suppress only the relevant reminder projection; they never
change Task planning/deadline state. The added fields are persisted with backward-compatible
defaults for older SQLite preference rows.

## Deterministic aggregation and autonomous reminders

Ordinary duplicate candidates with the same active aggregation key can aggregate into one
notification and increment `occurrence_count`.

Periodic reminder evaluation uses `NotificationService.create_once(...)`. An unchanged canonical
condition returns the existing active notification without incrementing occurrence count and
without redelivering external channels. Dismissed or archived notifications are no longer active,
so a later evaluation may surface a still-relevant source condition again.

Issue #88 integration derives assignment, dependency, approaching-deadline, and overdue attention
from `TaskManagementView`. #75 does not parse Task planning metadata into a competing truth. The
autonomous `NotificationRuntime` runs reminder evaluation on its normal poll tick and applies each
recipient's configured lead time.

## Canonical event projection

`NotificationRuntime` consumes the canonical `EventRepository` directly. Task terminal events
(`task.succeeded`, `task.failed`) are projected through the registered notification rules. A
durable processed-event cursor prevents replay duplication after restart.

The runtime is intentionally best-effort with respect to attention. A source-domain transition is
never rolled back or reported as failed merely because notification projection failed.

## Completed source-domain wiring

The completed source domains requested by the #75 hardening audit are connected to
`NotificationService`, not left as unused candidate helpers:

- **#15 Approval:** `AuthorizationGate` exposes a best-effort Approval lifecycle observer.
  Newly-created pending approvals emit `required`; approved/rejected/cancelled decisions emit
  `resolved`. The Notification composition consumes those events through
  `approval_required_candidate(...)` / `approval_resolved_candidate(...)`. Required-approval
  recipients are resolved by an explicit `ApprovalRecipientResolver`; #75 never assumes that the
  requester is also the approver. Observer failures cannot change authoritative Approval state.
- **#76 Accounting:** the Notification composition fans out the existing synchronous budget
  threshold sink. Thresholds with an explicit canonical budget owner are queued and drained by the
  autonomous Notification runtime into warning/exceeded resource attention. Missing/invalid owner
  metadata is skipped rather than guessed, and Accounting ingestion remains authoritative.
- **#18 Automation:** Automation delivery `failed`/`rejected` audit events are projected directly
  to canonical Automation notifications with stable per-delivery aggregation identity. Automation
  lifecycle success/failure remains authoritative if attention projection fails.
- **#44 Connectors:** `register_connector_control_plane()` discovers the optional provider-neutral
  `connector_health_event_sink` on the Notification Control Plane. A real `connection.health`
  transition to `degraded` or `error` projects Connector attention. #44 commits the Connection
  health/status first; a Notification failure cannot change the health-check result.

Issue #88 Task-management change projections remain directly integrated as well.

## Follow-up-domain seams

Implemented seams that deliberately remain opaque until their owning domains define final
vocabulary include:

- #86 Verification keyed by canonical `verification_*` ID plus an attention label;
- #87 membership/invitation keyed by canonical `membership_*` ID plus canonical recipient scope;
- provider-neutral agent-input, Node/Worker and similar attention through
  `canonical_attention_candidate(...)`.

These seams do not invent lifecycle enums or parallel truth. Their owning domains supply canonical
resource references and recipient policy.

## Control Plane

The public Control Plane exposes canonical notification resources:

- `GET /api/v1/notifications`
- `GET /api/v1/notifications/{notification_id}`
- `GET /api/v1/notification-preferences`
- `GET /api/v1/notification-preferences/{recipient_id}`

Commands:

- `notification.mark-read`
- `notification.mark-all-read`
- `notification.acknowledge`
- `notification.dismiss`
- `notification.archive`
- `notification.preference.update`
- `notification.delivery.retry`

Notifications are **built-in private resources**, not generic extensions. They appear in the
canonical manifest/OpenAPI but remain excluded from generic extension discovery. Global Search
uses a separate platform-owned, privacy-minimized derived projection rather than enumerating the
recipient-scoped public collection with a synthetic system actor.

OpenAPI publishes an `x-notifications` descriptor with recipient-scoped visibility,
`search_indexed: true`, `search_projection: privacy-minimized-derived-state`, and
`source_of_truth: false`.

## Global Search

Canonical global Search can discover Notifications by exact Notification ID and by safe metadata:
category, severity, attention state, source-resource type/ID, project/workspace scope, and the
canonical Task/Run/Approval/Verification/Node/Automation/Membership references when present.

The Search document intentionally does **not** contain the Notification's structured summary,
original title, action payloads, aggregation key, correlation/causation data, delivery metadata,
delivery attempts, channel/provider data, or recipient identity as searchable text. The result
title is a synthetic category/severity label and the result summary is empty.

A provider candidate is never directly exposed. Before Search emits an item, count or snippet the
Control Plane:

1. derives the caller's canonical Notification recipient from the authenticated request context;
2. requires an exact match with the indexed recipient owner scope;
3. honors `in_app_enabled`;
4. reloads the canonical Notification record;
5. rejects stale state projections;
6. rechecks current source-resource authorization; and
7. rechecks canonical `notification:list` authorization.

This makes exact-ID lookup non-disclosing: knowing another recipient's `notification_*` ID does not
reveal its existence. Revoked source access also removes the candidate from visible Search counts
without requiring Search to become an authorization authority.

Search is derived state only. A full rebuild reads current canonical repository rows and therefore
propagates state changes and repository deletion. Archived and expired Notifications are omitted
from the rebuild snapshot, so retention/lifecycle changes cannot leave them discoverable in the
reconstructed index. Search never mutates Notification or source-domain state.

## Live updates and application lifecycle

`GET /api/v1/notifications/stream` provides recipient-scoped Server-Sent Events.

The in-process `NotificationLiveHub` has bounded reconnect replay only. It is not durable truth.
When a cursor is no longer available, clients refresh the canonical inbox through the Control
Plane.

The production ASGI lifespan starts both the autonomous Automation and Notification runtimes and
stops both on shutdown. Startup failure unwinds any runtime already started, and shutdown reports
runtime-stop failures rather than silently abandoning them.

## External delivery

External channels use the replaceable `NotificationDeliveryChannel` boundary. The baseline does
not require any paid provider or Connector implementation.

Delivery behavior includes:

- stable per-notification/channel idempotency identity;
- persistent reference delivery attempts in the durable runtime profile;
- retryable/permanent/unavailable outcomes;
- restart-safe dedupe so a delivered attempt is not sent again after repository reconstruction;
- an `UnavailableDeliveryChannel` fixture proving external delivery is optional;
- explicit retry through the Control Plane;
- quiet-hours suppression for external channels while retaining in-app attention.

A future Connector-backed delivery-channel implementation must resolve provider-native
destinations only after canonical recipient authorization and must not make provider delivery
state canonical platform state.

## Security and redaction

Structured notification summaries, delivery metadata, and live events pass through the existing
redaction boundary. Approval projections intentionally omit proposed payload references and exact
action digests from notification summaries.

Unread counts, inbox resources, and Search results are recipient-scoped and current-source-
authorized. Search indexes only the minimized derived metadata described above; inaccessible
notifications cannot contribute to visible result counts, snippets, or exact-ID responses. Source
navigation is not an authorization bypass.

## Frontend

The frontend Notification center consumes only the versioned Control Plane:

- list/filter notifications;
- unread count;
- mark read / mark all read;
- acknowledge, dismiss, archive;
- preference updates;
- source/action navigation;
- live SSE refresh with canonical inbox recovery.

The frontend does not read notification persistence, source repositories, or adapter-native state
directly.

## Testing boundary

Issue #75 hardening coverage includes:

- production-shaped Task -> Event -> Notification -> restart projection;
- runtime cursor replay/dedupe after restart;
- Approval required/resolved through the real `AuthorizationGate` observer path;
- explicit Approval-recipient resolution without requester/approver inference;
- #76 budget threshold -> runtime tick -> Notification projection;
- #18 Automation failure event -> Notification projection;
- #44 real Connector health transition -> Notification projection;
- source-domain observer failure isolation from authoritative Approval/Automation/Connector state;
- #88 assignment/dependency/deadline projections and autonomous reminder idempotence;
- reminder preference and quiet-hours behavior;
- preference backward compatibility across SQLite restart;
- current source-authorization revocation across inbox list/get/count/actions/mark-all-read;
- team recipient authorization using the real request principal rather than recipient ID;
- acknowledged-state preservation under mark-read;
- external delivery persistence and restart-safe dedupe;
- SSE recipient isolation/reconnect behavior and ASGI dual-runtime lifecycle;
- exact-ID and safe metadata Notification Search;
- Search recipient/source authorization and non-disclosing counts;
- Search payload minimization plus archive/expiry/rebuild behavior;
- private-inbox exclusion from generic extension discovery;
- frontend typecheck/tests/build plus single-node installation smoke.
