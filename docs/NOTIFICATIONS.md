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
`Approval required` notification does not approve the Approval.

## Persistence

The domain is defined by repository protocols. The reference implementation includes:

- `InMemoryNotificationRepository` and in-memory preference/delivery stores for tests and
  ephemeral deployments;
- `SqliteNotificationRepository` and `SqliteNotificationPreferenceRepository` as restart-safe
  reference persistence.

SQLite is an implementation choice, not a canonical requirement.

## Recipient identity and authorization

Recipients are canonical platform identities. Provider-native account IDs, e-mail addresses, or
connector-specific identities are never canonical notification recipients.

`RecipientRef` requires the existing canonical ID vocabulary (`user_*`, `team_*`,
`organization_*`). Control Plane inbox access derives the recipient from authenticated canonical
owner context. Cross-recipient reads use not-found semantics so inaccessible notification
existence is not leaked.

The optional `RecipientEligibilityGuard` is the integration boundary for current authorization,
membership, suspension, or removal state. It is evaluated before **new** attention is created.
A #15/#87-aware implementation can therefore stop newly unauthorized delivery without deleting
historical source events or already-created notification records. The default implementation
allows recipients when no membership-aware provider is configured.

Notification actions and source links are navigation/command metadata only. The owning Control
Plane subsystem must authorize the eventual read or command again.

## Preferences

Per-recipient preferences support:

- enabled categories;
- minimum severity;
- optional project filtering;
- mute state;
- in-app enable/disable;
- configured external channel IDs;
- duplicate aggregation preference.

Preferences suppress attention projection/delivery; they do not suppress or alter canonical
source events.

## Deterministic aggregation and reminders

Ordinary duplicate candidates with the same active aggregation key can aggregate into one
notification and increment `occurrence_count`.

Periodic reminder evaluation uses `NotificationService.create_once(...)`. An unchanged canonical
condition returns the existing active notification without incrementing occurrence count and
without redelivering external channels. Dismissed or archived notifications are no longer active,
so a later evaluation may surface a still-relevant source condition again.

Issue #88 integration derives assignment, dependency, approaching-deadline, and overdue attention
from `TaskManagementView`. #75 does not parse Task planning metadata into a competing truth.

## Event and domain projections

Implemented projections/seams include:

- canonical Task terminal events (`task.succeeded`, `task.failed`);
- Approval required and Approval resolved using #15's canonical Approval record/status;
- #76 accounting/resource budget threshold attention;
- #88 assignment, dependency, approaching-deadline, and overdue attention;
- an opaque #86 Verification seam keyed by canonical `verification_*` ID plus an attention label;
- an opaque #87 membership/invitation seam keyed by canonical `membership_*` ID plus an attention
  label and canonical recipient scope.

The #86/#87 seams intentionally do not define Verification or membership status enums. Those
issues remain the owners of their domain vocabularies and lifecycles; their future adapters can
pass canonical signals into #75.

Other event sources (agent input, worker/node health, Automation failures, security events,
Connector failures) should follow the same projection rule: source state remains authoritative in
the owning domain, while #75 owns only the attention record.

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
canonical manifest/OpenAPI but are excluded from `registered_collections` /
`registered_commands`. This prevents generic extension discovery and the global Search index from
enumerating private recipient-scoped inboxes with a system context.

OpenAPI publishes an `x-notifications` descriptor with recipient-scoped visibility,
`search_indexed: false`, and `source_of_truth: false`.

## Live updates

`GET /api/v1/notifications/stream` provides recipient-scoped Server-Sent Events.

The in-process `NotificationLiveHub` has bounded reconnect replay only. It is not durable truth.
When a cursor is no longer available, clients must refresh the canonical inbox through the
Control Plane. The runtime-complete ASGI composition keeps the Automation runtime lifespan inside
the Notification SSE router.

## External delivery

External channels use the replaceable `NotificationDeliveryChannel` boundary. The baseline does
not require any paid provider or Connector implementation.

Delivery behavior includes:

- stable per-notification/channel idempotency identity;
- persisted/reference delivery attempts;
- retryable/permanent/unavailable outcomes;
- an `UnavailableDeliveryChannel` fixture proving external delivery is optional;
- explicit retry through the Control Plane.

A future Connector-backed implementation must resolve provider-native destinations only after
canonical recipient authorization and must not make provider delivery state canonical platform
state.

## Security and redaction

Structured notification summaries, delivery metadata, and live events pass through the existing
redaction boundary. Approval projections intentionally omit proposed payload references and exact
action digests from notification summaries.

Unread counts and snippets are recipient-scoped. Private notifications are excluded from global
Search indexing. Source navigation is not an authorization bypass.

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

Issue #75 coverage includes:

- Task completed/failed projection;
- Approval required/resolved projection without sensitive payload leakage;
- Verification required/changes-requested seam;
- organization/membership recipient scope;
- removed/suspended recipient eligibility fixture;
- #88 assignment/dependency/deadline projections and reminder idempotence;
- preference filtering;
- cross-recipient isolation and authorization;
- duplicate aggregation;
- external channel unavailable/retry/dedupe behavior;
- SQLite restart persistence;
- SSE recipient isolation/reconnect behavior;
- private-inbox exclusion from global Search/extension discovery;
- frontend typecheck/tests/build.
