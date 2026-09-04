import { useCallback, useEffect, useMemo, useState } from "react";
import {
  NotificationClient,
  type CanonicalNotification,
  type CanonicalNotificationPreference,
  type NotificationCategory,
  type NotificationSeverity,
  type NotificationState,
} from "../api/notifications";
import {
  NotificationEventStream,
  describeLiveStreamError,
  type LiveConnectionState,
} from "../api/live";
import type { JsonValue, Page } from "../api/types";
import { AppLink } from "../app/router";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const CATEGORIES: NotificationCategory[] = [
  "task",
  "approval",
  "verification",
  "agent_input",
  "deadline",
  "assignment",
  "dependency",
  "worker",
  "automation",
  "security",
  "resource",
  "connector",
  "membership",
  "general",
];
const SEVERITIES: NotificationSeverity[] = ["info", "warning", "error", "critical"];
const STATES: NotificationState[] = ["unread", "read", "acknowledged", "dismissed", "archived"];

export function NotificationsPage({ client }: { client: NotificationClient }) {
  const [page, setPage] = useState<Page<CanonicalNotification> | null>(null);
  const [preference, setPreference] = useState<CanonicalNotificationPreference | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<LiveConnectionState>("connecting");
  const [category, setCategory] = useState<NotificationCategory | "">("");
  const [severity, setSeverity] = useState<NotificationSeverity | "">("");
  const [state, setState] = useState<NotificationState | "">("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [notifications, loadedPreference] = await Promise.all([
        client.list({
          limit: 100,
          sort: "updated_at",
          direction: "desc",
          filters: {
            ...(category ? { category } : {}),
            ...(severity ? { severity } : {}),
            ...(state ? { state } : {}),
          },
        }),
        client.preference(),
      ]);
      setPage(notifications);
      setPreference(loadedPreference);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [category, client, severity, state]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const stream = new NotificationEventStream({
      baseUrl: client.baseUrl,
      onEvent: () => {
        setStreamError(null);
        void load();
      },
      onError: (nextError) => setStreamError(describeLiveStreamError(nextError)),
      onState: setStreamState,
    });
    stream.open();
    return () => stream.close();
  }, [client.baseUrl, load]);

  const mutate = useCallback(
    async (notificationId: string, action: () => Promise<unknown>) => {
      setBusyId(notificationId);
      try {
        await action();
        await load();
      } catch (nextError) {
        setError(nextError);
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  const updatePreference = useCallback(
    async (update: Parameters<NotificationClient["updatePreference"]>[1]) => {
      if (!preference) return;
      setBusyId("preferences");
      try {
        setPreference(await client.updatePreference(preference.id, update));
        await load();
      } catch (nextError) {
        setError(nextError);
      } finally {
        setBusyId(null);
      }
    },
    [client, load, preference],
  );

  const visibleUnread = page?.items.filter((item) => item.state === "unread").length ?? 0;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical user attention</p>
        <h1>Notifications</h1>
        <p>
          Attention projections over canonical Tasks, Approvals, accounting thresholds and other
          platform resources. Source lifecycles remain authoritative in their owning domains.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Unread" value={preference?.unread_count ?? "—"} />
        <Metric label="Visible" value={page?.total ?? "—"} />
        <Metric label="Unread in view" value={page ? visibleUnread : "—"} />
        <Metric label="Live updates" value={liveLabel(streamState)} />
      </div>

      {streamError ? (
        <Card title="Live update status">
          <p>{streamError}</p>
          <p>The canonical inbox remains available; refresh or reconnect can recover state.</p>
        </Card>
      ) : null}

      <Card title="Inbox filters">
        <div className="actions">
          <label>
            Category
            <select value={category} onChange={(event) => setCategory(event.target.value as NotificationCategory | "")}>
              <option value="">All</option>
              {CATEGORIES.map((value) => <option value={value} key={value}>{label(value)}</option>)}
            </select>
          </label>
          <label>
            Severity
            <select value={severity} onChange={(event) => setSeverity(event.target.value as NotificationSeverity | "")}>
              <option value="">All</option>
              {SEVERITIES.map((value) => <option value={value} key={value}>{label(value)}</option>)}
            </select>
          </label>
          <label>
            State
            <select value={state} onChange={(event) => setState(event.target.value as NotificationState | "")}>
              <option value="">All active</option>
              {STATES.map((value) => <option value={value} key={value}>{label(value)}</option>)}
            </select>
          </label>
          <button onClick={() => void load()}>Refresh</button>
          <button
            disabled={!preference || preference.unread_count === 0 || busyId !== null}
            onClick={() => {
              setBusyId("all");
              void client.markAllRead().then(load).catch(setError).finally(() => setBusyId(null));
            }}
          >
            Mark all read
          </button>
        </div>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!page && !error ? <LoadingState /> : null}
      {page ? (
        <Card title="Notification center">
          <NotificationList
            notifications={page.items}
            busyId={busyId}
            client={client}
            mutate={mutate}
          />
        </Card>
      ) : null}

      {preference ? (
        <Card title="Attention preferences">
          <div className="grid-two">
            <div className="stack compact-stack">
              <label>
                Minimum severity
                <select
                  value={preference.minimum_severity}
                  disabled={busyId === "preferences"}
                  onChange={(event) => void updatePreference({
                    minimum_severity: event.target.value as NotificationSeverity,
                  })}
                >
                  {SEVERITIES.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                </select>
              </label>
              <Toggle
                label="Mute notifications"
                checked={preference.muted}
                disabled={busyId === "preferences"}
                onChange={(checked) => void updatePreference({ muted: checked })}
              />
              <Toggle
                label="Show in application"
                checked={preference.in_app_enabled}
                disabled={busyId === "preferences"}
                onChange={(checked) => void updatePreference({ in_app_enabled: checked })}
              />
              <Toggle
                label="Aggregate duplicates"
                checked={preference.aggregate_duplicates}
                disabled={busyId === "preferences"}
                onChange={(checked) => void updatePreference({ aggregate_duplicates: checked })}
              />
            </div>
            <div>
              <strong>Enabled categories</strong>
              <div className="stack compact-stack">
                {CATEGORIES.map((value) => (
                  <Toggle
                    key={value}
                    label={label(value)}
                    checked={preference.enabled_categories.includes(value)}
                    disabled={busyId === "preferences"}
                    onChange={(checked) => {
                      const next = checked
                        ? [...preference.enabled_categories, value]
                        : preference.enabled_categories.filter((item) => item !== value);
                      void updatePreference({ enabled_categories: next });
                    }}
                  />
                ))}
              </div>
            </div>
          </div>
          <p>
            External channels: {preference.external_channels.length
              ? preference.external_channels.join(", ")
              : "none configured"}.
          </p>
        </Card>
      ) : null}
    </div>
  );
}

function NotificationList({
  notifications,
  busyId,
  client,
  mutate,
}: {
  notifications: CanonicalNotification[];
  busyId: string | null;
  client: NotificationClient;
  mutate: (notificationId: string, action: () => Promise<unknown>) => Promise<void>;
}) {
  if (notifications.length === 0) {
    return <EmptyState title="No notifications in this view" />;
  }
  return (
    <div className="stack">
      {notifications.map((notification) => (
        <article className="card notification-card" key={notification.id}>
          <div className="detail-header">
            <div>
              <p className="eyebrow">{label(notification.category)} · {notification.severity}</p>
              <h2>{notification.title}</h2>
              <CanonicalId value={notification.id} />
            </div>
            <div className="detail-status">
              <StatusBadge value={notification.state} />
              {notification.occurrence_count > 1 ? <span>×{notification.occurrence_count}</span> : null}
            </div>
          </div>
          <Summary summary={notification.summary} />
          <p>
            Source: <SourceLink notification={notification} /> · updated {formatDate(notification.updated_at)}
          </p>
          <div className="actions">
            {notification.state === "unread" ? (
              <button
                disabled={busyId === notification.id}
                onClick={() => void mutate(notification.id, () => client.markRead(notification.id))}
              >
                Mark read
              </button>
            ) : null}
            {!(["acknowledged", "dismissed", "archived"] as NotificationState[]).includes(notification.state) ? (
              <button
                disabled={busyId === notification.id}
                onClick={() => void mutate(notification.id, () => client.acknowledge(notification.id))}
              >
                Acknowledge
              </button>
            ) : null}
            {notification.state !== "dismissed" && notification.state !== "archived" ? (
              <button
                disabled={busyId === notification.id}
                onClick={() => void mutate(notification.id, () => client.dismiss(notification.id))}
              >
                Dismiss
              </button>
            ) : null}
            {notification.state !== "archived" ? (
              <button
                disabled={busyId === notification.id}
                onClick={() => void mutate(notification.id, () => client.archive(notification.id))}
              >
                Archive
              </button>
            ) : null}
            {notification.actions.map((action) => action.href ? (
              <AppLink href={action.href} key={action.action_id}>{action.label}</AppLink>
            ) : null)}
          </div>
          {notification.delivery.attempts.length ? (
            <details>
              <summary>External delivery attempts ({notification.delivery.attempts.length})</summary>
              <div className="stack compact-stack">
                {notification.delivery.attempts.map((attempt) => (
                  <div key={attempt.id}>
                    <strong>{attempt.channel}</strong>: {attempt.status} · attempt {attempt.attempt} · {formatDate(attempt.attempted_at)}
                    {attempt.status === "retryable_failure" || attempt.status === "unavailable" ? (
                      <button
                        disabled={busyId === notification.id}
                        onClick={() => void mutate(
                          notification.id,
                          () => client.retryDelivery(notification.id, attempt.channel),
                        )}
                      >
                        Retry
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function Summary({ summary }: { summary: Record<string, JsonValue> }) {
  const entries = Object.entries(summary);
  if (!entries.length) return null;
  return (
    <dl className="definition-list">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{displayValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function SourceLink({ notification }: { notification: CanonicalNotification }) {
  const href = sourceHref(notification.source.resource_type, notification.source.resource_id);
  if (!href) {
    return <CanonicalId value={notification.source.resource_id} />;
  }
  return (
    <AppLink href={href}>
      {notification.source.resource_type}:<CanonicalId value={notification.source.resource_id} />
    </AppLink>
  );
}

function sourceHref(resourceType: string, resourceId: string): string | null {
  const routes: Record<string, string> = {
    task: "/tasks/",
    run: "/runs/",
    approval: "/approvals/",
    automation: "/automations/",
  };
  const prefix = routes[resourceType];
  return prefix ? `${prefix}${encodeURIComponent(resourceId)}` : null;
}

function Toggle({
  label: text,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      {text}
    </label>
  );
}

function Metric({ label: text, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{text}</span><strong>{value}</strong></div>;
}

function liveLabel(state: LiveConnectionState): string {
  switch (state) {
    case "open": return "Live";
    case "reconnecting": return "Reconnecting";
    case "closed": return "Closed";
    default: return "Connecting";
  }
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function displayValue(value: JsonValue): string {
  if (value === null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
