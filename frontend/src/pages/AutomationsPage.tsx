import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AutomationClient,
  type AutomationOverlapPolicy,
  type AutomationTriggerInput,
  type AutomationTriggerType,
  type CanonicalAutomation,
  type CanonicalAutomationDelivery,
  type CreateAutomationInput,
  type MissedSchedulePolicy,
  type UpdateAutomationInput,
} from "../api/automations";
import { ControlPlaneCollectionClient } from "../api/collections";
import type { JsonValue, Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  Card,
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const AUTOMATION_COLLECTION = "automations";
const DELIVERY_COLLECTION = "automation-deliveries";

interface AutomationDraft {
  name: string;
  description: string;
  projectId: string;
  workspaceId: string;
  triggerType: AutomationTriggerType;
  timezone: string;
  at: string;
  intervalSeconds: string;
  eventType: string;
  filtersJson: string;
  webhookSource: string;
  verificationRef: string;
  missedSchedulePolicy: MissedSchedulePolicy;
  taskTitle: string;
  taskObjective: string;
  taskProjectId: string;
  taskWorkspaceId: string;
  taskPayloadJson: string;
  retryMaxAttempts: string;
  retryBaseBackoffSeconds: string;
  overlapPolicy: AutomationOverlapPolicy;
}

export function AutomationsPage({
  collections,
  automations,
}: {
  collections: ControlPlaneCollectionClient;
  automations: AutomationClient;
}) {
  const [page, setPage] = useState<Page<CanonicalAutomation> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const pagination = useCursorPagination("automations:id:asc");

  const load = useCallback(async () => {
    try {
      setPage(
        await collections.list<CanonicalAutomation>(AUTOMATION_COLLECTION, {
          limit: 50,
          cursor: pagination.cursor,
          sort: "id",
          direction: "asc",
        }),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [collections, pagination.cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  const enabled = page?.items.filter((item) => item.state === "enabled").length ?? "—";
  const paused = page?.items.filter((item) => item.state === "paused").length ?? "—";

  async function create(draft: AutomationDraft) {
    setCreating(true);
    try {
      await automations.create(toCreateInput(draft));
      setCreateError(null);
      setFormKey((value) => value + 1);
      await load();
    } catch (nextError) {
      setCreateError(nextError);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical automation management</p>
        <h1>Automations</h1>
        <p>
          Schedules, webhook definitions, platform-event triggers and manual test triggers all
          create ordinary canonical Tasks. This page never calls orchestrators, executors or
          Workers directly.
        </p>
      </header>

      <div className="metrics">
        <Metric label="Automations" value={page?.total ?? "—"} />
        <Metric label="Enabled on page" value={enabled} />
        <Metric label="Paused on page" value={paused} />
      </div>

      <Card title="Automation inventory">
        <div className="actions">
          <button onClick={() => void load()}>Refresh</button>
        </div>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!page && !error ? <LoadingState /> : null}
        {page ? <AutomationTable automations={page.items} /> : null}
        {page ? (
          <PaginationControls
            page={page}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(page.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Create automation">
        <p>
          Identity and ownership are taken from the authenticated #36/#15 request context. Secret
          values must be referenced by canonical configuration; webhook verification uses only
          `verification_ref` here.
        </p>
        {createError ? <ErrorState error={createError} /> : null}
        <AutomationForm
          key={formKey}
          includeScope
          submitLabel={creating ? "Creating…" : "Create automation"}
          disabled={creating}
          onSubmit={create}
        />
      </Card>
    </div>
  );
}

export function AutomationDetailPage({
  collections,
  automations,
  automationId,
}: {
  collections: ControlPlaneCollectionClient;
  automations: AutomationClient;
  automationId: string;
}) {
  const [automation, setAutomation] = useState<CanonicalAutomation | null>(null);
  const [deliveryPage, setDeliveryPage] = useState<Page<CanonicalAutomationDelivery> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [testPayload, setTestPayload] = useState("{}");
  const [lastTest, setLastTest] = useState<CanonicalAutomationDelivery | null>(null);
  const deliveryPagination = useCursorPagination(`automation-deliveries:${automationId}:id:asc`);

  const loadAutomation = useCallback(async () => {
    const loaded = await collections.get<CanonicalAutomation>(AUTOMATION_COLLECTION, automationId);
    setAutomation(loaded);
    return loaded;
  }, [automationId, collections]);

  const loadDeliveries = useCallback(async () => {
    setDeliveryPage(
      await collections.list<CanonicalAutomationDelivery>(DELIVERY_COLLECTION, {
        limit: 25,
        cursor: deliveryPagination.cursor,
        sort: "id",
        direction: "asc",
        filters: { automation_id: automationId },
      }),
    );
  }, [automationId, collections, deliveryPagination.cursor]);

  const load = useCallback(async () => {
    try {
      await Promise.all([loadAutomation(), loadDeliveries()]);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [loadAutomation, loadDeliveries]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(action: () => Promise<CanonicalAutomation>) {
    setBusy(true);
    try {
      const updated = await action();
      setAutomation(updated);
      setActionError(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function update(draft: AutomationDraft) {
    await runAction(() => automations.update(automationId, toUpdateInput(draft)));
  }

  async function runTest() {
    setBusy(true);
    try {
      const payload = parseJsonObject(testPayload, "manual test payload");
      const delivery = await automations.test(automationId, payload);
      setLastTest(delivery);
      setActionError(null);
      await Promise.all([loadAutomation(), loadDeliveries()]);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function retry(deliveryId: string) {
    setBusy(true);
    try {
      await automations.retryDelivery(deliveryId);
      setActionError(null);
      await loadDeliveries();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  }

  if (error && automation === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (automation === null) return <LoadingState />;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Automation</p>
          <h1>{automation.name}</h1>
          <CanonicalId value={automation.id} />
        </div>
        <div className="detail-status">
          <StatusBadge value={automation.state} />
          <span>revision {automation.revision}</span>
        </div>
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Lifecycle controls">
        <div className="actions">
          {automation.state === "enabled" ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.pause(automation.id))}>
              Pause
            </button>
          ) : null}
          {automation.state === "paused" ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.resume(automation.id))}>
              Resume
            </button>
          ) : null}
          {automation.state !== "disabled" ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.disable(automation.id))}>
              Disable
            </button>
          ) : null}
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        <p>
          State transitions go through the canonical #18 commands and remain subject to server-side
          authorization. A disabled automation is not silently re-created or replaced by frontend
          state.
        </p>
      </Card>

      <div className="grid-two">
        <Card title="Trigger">
          <DefinitionList
            values={{
              type: automation.trigger.type,
              timezone: automation.trigger.timezone,
              at: formatDate(automation.trigger.at),
              interval_seconds: automation.trigger.interval_seconds ?? "—",
              event_type: automation.trigger.event_type ?? "—",
              webhook_source: automation.trigger.webhook_source ?? "—",
              verification_ref: automation.trigger.verification_ref ?? "—",
              missed_schedule_policy: automation.trigger.missed_schedule_policy,
            }}
          />
        </Card>
        <Card title="Task template">
          <DefinitionList
            values={{
              title: automation.task_template.title,
              objective: automation.task_template.objective,
              project: automation.task_template.project_id ?? "—",
              workspace: automation.task_template.workspace_id ?? "—",
              owner: `${automation.owner_ref.type}:${automation.owner_ref.id}`,
            }}
          />
        </Card>
      </div>

      <div className="grid-two">
        <Card title="Scheduling & retry">
          <DefinitionList
            values={{
              overlap_policy: automation.overlap_policy,
              max_attempts: automation.retry_policy.max_attempts,
              base_backoff_seconds: automation.retry_policy.base_backoff_seconds,
              last_evaluated: formatDate(automation.last_evaluated_at),
              next_evaluation: formatDate(automation.next_evaluation_at),
            }}
          />
        </Card>
        <Card title="Canonical scope">
          <DefinitionList
            values={{
              project: automation.project_id ?? "—",
              workspace: automation.workspace_id ?? "—",
              principal: automation.identity.principal_ref,
              created: formatDate(automation.created_at),
              updated: formatDate(automation.updated_at),
            }}
          />
        </Card>
      </div>

      <Card title="Edit configuration">
        <p>
          #18 updates the versioned trigger, task template, retry and overlap configuration. The
          automation's canonical owner/project/workspace scope is not rewritten by this command.
        </p>
        <AutomationForm
          initial={automation}
          submitLabel={busy ? "Saving…" : "Save revision"}
          disabled={busy}
          onSubmit={update}
        />
      </Card>

      <Card title="Manual test trigger">
        <p>
          A manual test still creates work through the normal Automation delivery path and canonical
          Task lifecycle. It is not a browser-side dry run.
        </p>
        <label>
          Test payload JSON
          <textarea
            rows={5}
            value={testPayload}
            onChange={(event) => setTestPayload(event.target.value)}
          />
        </label>
        <div className="actions">
          <button disabled={busy || automation.state !== "enabled"} onClick={() => void runTest()}>
            Run manual test
          </button>
        </div>
        {lastTest ? (
          <p>
            Last test delivery: <CanonicalId value={lastTest.id} /> · <StatusBadge value={lastTest.status} />
          </p>
        ) : null}
      </Card>

      <Card title="Delivery history">
        <p>
          Delivery payload values are intentionally not rendered in the history table. Canonical
          provenance, status, generated Task references and errors remain visible.
        </p>
        {!deliveryPage && !error ? <LoadingState /> : null}
        {deliveryPage ? (
          <DeliveryTable deliveries={deliveryPage.items} busy={busy} onRetry={retry} />
        ) : null}
        {deliveryPage ? (
          <PaginationControls
            page={deliveryPage}
            pageNumber={deliveryPagination.pageNumber}
            hasPrevious={deliveryPagination.hasPrevious}
            onPrevious={deliveryPagination.previous}
            onRefresh={() => void loadDeliveries()}
            onNext={() => deliveryPagination.next(deliveryPage.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

function AutomationTable({ automations }: { automations: CanonicalAutomation[] }) {
  if (automations.length === 0) return <EmptyState title="No automations configured" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Automation</th><th>State</th><th>Trigger</th><th>Task template</th><th>Revision</th><th>Next evaluation</th>
          </tr>
        </thead>
        <tbody>
          {automations.map((automation) => (
            <tr key={automation.id}>
              <td>
                <AppLink href={`/automations/${encodeURIComponent(automation.id)}`}>
                  {automation.name}<br /><CanonicalId value={automation.id} />
                </AppLink>
              </td>
              <td><StatusBadge value={automation.state} /></td>
              <td>{triggerSummary(automation)}</td>
              <td>{automation.task_template.title}</td>
              <td>{automation.revision}</td>
              <td>{formatDate(automation.next_evaluation_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DeliveryTable({
  deliveries,
  busy,
  onRetry,
}: {
  deliveries: CanonicalAutomationDelivery[];
  busy: boolean;
  onRetry: (deliveryId: string) => Promise<void>;
}) {
  if (deliveries.length === 0) return <EmptyState title="No deliveries for this automation" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Delivery</th><th>Status</th><th>Source</th><th>Attempt</th><th>Generated Task</th><th>Error</th><th>Received</th><th /></tr>
        </thead>
        <tbody>
          {deliveries.map((delivery) => (
            <tr key={delivery.id}>
              <td><CanonicalId value={delivery.id} /></td>
              <td><StatusBadge value={delivery.status} /></td>
              <td>{delivery.source}</td>
              <td>{delivery.attempt}</td>
              <td>
                {delivery.generated_task_id ? (
                  <AppLink href={`/tasks/${encodeURIComponent(delivery.generated_task_id)}`}>
                    <CanonicalId value={delivery.generated_task_id} />
                  </AppLink>
                ) : "—"}
              </td>
              <td>{delivery.error_code ? `${delivery.error_code}: ${delivery.error_message ?? ""}` : "—"}</td>
              <td>{formatDate(delivery.received_at)}</td>
              <td>
                {delivery.status === "failed" ? (
                  <button disabled={busy} onClick={() => void onRetry(delivery.id)}>Retry</button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AutomationForm({
  initial,
  includeScope = false,
  submitLabel,
  disabled,
  onSubmit,
}: {
  initial?: CanonicalAutomation;
  includeScope?: boolean;
  submitLabel: string;
  disabled: boolean;
  onSubmit: (draft: AutomationDraft) => Promise<void>;
}) {
  const initialDraft = useMemo(() => draftFromAutomation(initial), [initial]);
  const [draft, setDraft] = useState<AutomationDraft>(initialDraft);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => setDraft(initialDraft), [initialDraft]);

  function field<K extends keyof AutomationDraft>(name: K, value: AutomationDraft[K]) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      validateDraft(draft);
      setFormError(null);
      await onSubmit(draft);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  }

  const isSchedule = draft.triggerType === "one_time" || draft.triggerType === "recurring";

  return (
    <form className="stack" onSubmit={(event) => void submit(event)}>
      {formError ? <p role="alert">{formError}</p> : null}
      <div className="form-grid">
        <label>
          Name
          <input required value={draft.name} onChange={(event) => field("name", event.target.value)} />
        </label>
        <label>
          Description
          <input value={draft.description} onChange={(event) => field("description", event.target.value)} />
        </label>
        {includeScope ? (
          <>
            <label>
              Automation project ID
              <input value={draft.projectId} onChange={(event) => field("projectId", event.target.value)} />
            </label>
            <label>
              Automation workspace ID
              <input value={draft.workspaceId} onChange={(event) => field("workspaceId", event.target.value)} />
            </label>
          </>
        ) : null}
      </div>

      <h3>Trigger</h3>
      <div className="form-grid">
        <label>
          Trigger type
          <select value={draft.triggerType} onChange={(event) => field("triggerType", event.target.value as AutomationTriggerType)}>
            <option value="one_time">One time</option>
            <option value="recurring">Recurring</option>
            <option value="webhook">Webhook</option>
            <option value="platform_event">Platform event</option>
            <option value="manual">Manual</option>
          </select>
        </label>
        <label>
          Timezone
          <input value={draft.timezone} onChange={(event) => field("timezone", event.target.value)} />
        </label>
        {isSchedule ? (
          <label>
            First fire / at (ISO-8601 with offset)
            <input
              required
              placeholder="2026-09-04T08:00:00+02:00"
              value={draft.at}
              onChange={(event) => field("at", event.target.value)}
            />
          </label>
        ) : null}
        {draft.triggerType === "recurring" ? (
          <label>
            Interval seconds
            <input
              required
              min="0.001"
              step="any"
              type="number"
              value={draft.intervalSeconds}
              onChange={(event) => field("intervalSeconds", event.target.value)}
            />
          </label>
        ) : null}
        {draft.triggerType === "platform_event" ? (
          <label>
            Canonical event type
            <input required value={draft.eventType} onChange={(event) => field("eventType", event.target.value)} />
          </label>
        ) : null}
        {draft.triggerType === "webhook" ? (
          <>
            <label>
              Webhook source
              <input required value={draft.webhookSource} onChange={(event) => field("webhookSource", event.target.value)} />
            </label>
            <label>
              Verification reference
              <input value={draft.verificationRef} onChange={(event) => field("verificationRef", event.target.value)} />
            </label>
          </>
        ) : null}
        {isSchedule ? (
          <label>
            Missed schedule policy
            <select value={draft.missedSchedulePolicy} onChange={(event) => field("missedSchedulePolicy", event.target.value as MissedSchedulePolicy)}>
              <option value="coalesce">Coalesce</option>
              <option value="skip">Skip</option>
            </select>
          </label>
        ) : null}
      </div>
      <label>
        Trigger filters JSON
        <textarea rows={4} value={draft.filtersJson} onChange={(event) => field("filtersJson", event.target.value)} />
      </label>

      <h3>Task template</h3>
      <div className="form-grid">
        <label>
          Task title
          <input required value={draft.taskTitle} onChange={(event) => field("taskTitle", event.target.value)} />
        </label>
        <label>
          Task objective
          <input required value={draft.taskObjective} onChange={(event) => field("taskObjective", event.target.value)} />
        </label>
        <label>
          Task project ID
          <input value={draft.taskProjectId} onChange={(event) => field("taskProjectId", event.target.value)} />
        </label>
        <label>
          Task workspace ID
          <input value={draft.taskWorkspaceId} onChange={(event) => field("taskWorkspaceId", event.target.value)} />
        </label>
      </div>
      <label>
        Task payload JSON
        <textarea rows={5} value={draft.taskPayloadJson} onChange={(event) => field("taskPayloadJson", event.target.value)} />
      </label>

      <h3>Delivery policy</h3>
      <div className="form-grid">
        <label>
          Retry attempts
          <input min="1" type="number" value={draft.retryMaxAttempts} onChange={(event) => field("retryMaxAttempts", event.target.value)} />
        </label>
        <label>
          Base backoff seconds
          <input min="0" step="any" type="number" value={draft.retryBaseBackoffSeconds} onChange={(event) => field("retryBaseBackoffSeconds", event.target.value)} />
        </label>
        <label>
          Overlap policy
          <select value={draft.overlapPolicy} onChange={(event) => field("overlapPolicy", event.target.value as AutomationOverlapPolicy)}>
            <option value="skip_while_processing">Skip while processing</option>
            <option value="allow">Allow overlap</option>
          </select>
        </label>
      </div>

      <div className="actions">
        <button disabled={disabled} type="submit">{submitLabel}</button>
      </div>
    </form>
  );
}

function draftFromAutomation(automation?: CanonicalAutomation): AutomationDraft {
  return {
    name: automation?.name ?? "",
    description: automation?.description ?? "",
    projectId: automation?.project_id ?? "",
    workspaceId: automation?.workspace_id ?? "",
    triggerType: automation?.trigger.type ?? "manual",
    timezone: automation?.trigger.timezone ?? "UTC",
    at: automation?.trigger.at ?? "",
    intervalSeconds: automation?.trigger.interval_seconds?.toString() ?? "",
    eventType: automation?.trigger.event_type ?? "",
    filtersJson: JSON.stringify(automation?.trigger.filters ?? {}, null, 2),
    webhookSource: automation?.trigger.webhook_source ?? "",
    verificationRef: automation?.trigger.verification_ref ?? "",
    missedSchedulePolicy: automation?.trigger.missed_schedule_policy ?? "coalesce",
    taskTitle: automation?.task_template.title ?? "",
    taskObjective: automation?.task_template.objective ?? "",
    taskProjectId: automation?.task_template.project_id ?? "",
    taskWorkspaceId: automation?.task_template.workspace_id ?? "",
    taskPayloadJson: JSON.stringify(automation?.task_template.payload ?? {}, null, 2),
    retryMaxAttempts: automation?.retry_policy.max_attempts.toString() ?? "3",
    retryBaseBackoffSeconds: automation?.retry_policy.base_backoff_seconds.toString() ?? "1",
    overlapPolicy: automation?.overlap_policy ?? "skip_while_processing",
  };
}

function validateDraft(draft: AutomationDraft): void {
  if (!draft.name.trim()) throw new Error("name is required");
  if (!draft.taskTitle.trim()) throw new Error("task title is required");
  if (!draft.taskObjective.trim()) throw new Error("task objective is required");
  if (!draft.timezone.trim()) throw new Error("timezone is required");
  if ((draft.triggerType === "one_time" || draft.triggerType === "recurring") && !draft.at.trim()) {
    throw new Error("scheduled triggers require an ISO-8601 at timestamp");
  }
  if (draft.triggerType === "recurring") {
    const interval = Number(draft.intervalSeconds);
    if (!Number.isFinite(interval) || interval <= 0) throw new Error("recurring interval must be positive");
  }
  if (draft.triggerType === "platform_event" && !draft.eventType.trim()) {
    throw new Error("platform-event triggers require an event type");
  }
  if (draft.triggerType === "webhook" && !draft.webhookSource.trim()) {
    throw new Error("webhook triggers require a source");
  }
  const attempts = Number(draft.retryMaxAttempts);
  if (!Number.isInteger(attempts) || attempts < 1) throw new Error("retry attempts must be an integer of at least 1");
  const backoff = Number(draft.retryBaseBackoffSeconds);
  if (!Number.isFinite(backoff) || backoff < 0) throw new Error("base backoff must be non-negative");
  parseJsonObject(draft.filtersJson, "trigger filters");
  parseJsonObject(draft.taskPayloadJson, "task payload");
}

function toCreateInput(draft: AutomationDraft): CreateAutomationInput {
  return {
    name: draft.name.trim(),
    description: optional(draft.description),
    project_id: optional(draft.projectId),
    workspace_id: optional(draft.workspaceId),
    trigger: triggerInput(draft),
    task_template: {
      title: draft.taskTitle.trim(),
      objective: draft.taskObjective.trim(),
      project_id: optional(draft.taskProjectId),
      workspace_id: optional(draft.taskWorkspaceId),
      payload: parseJsonObject(draft.taskPayloadJson, "task payload"),
    },
    deduplication_strategy: "delivery_key",
    retry_policy: {
      max_attempts: Number(draft.retryMaxAttempts),
      base_backoff_seconds: Number(draft.retryBaseBackoffSeconds),
    },
    overlap_policy: draft.overlapPolicy,
  };
}

function toUpdateInput(draft: AutomationDraft): UpdateAutomationInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || "",
    trigger: triggerInput(draft),
    task_template: {
      title: draft.taskTitle.trim(),
      objective: draft.taskObjective.trim(),
      project_id: optional(draft.taskProjectId),
      workspace_id: optional(draft.taskWorkspaceId),
      payload: parseJsonObject(draft.taskPayloadJson, "task payload"),
    },
    deduplication_strategy: "delivery_key",
    retry_policy: {
      max_attempts: Number(draft.retryMaxAttempts),
      base_backoff_seconds: Number(draft.retryBaseBackoffSeconds),
    },
    overlap_policy: draft.overlapPolicy,
  };
}

function triggerInput(draft: AutomationDraft): AutomationTriggerInput {
  const trigger: AutomationTriggerInput = {
    type: draft.triggerType,
    timezone: draft.timezone.trim(),
    filters: parseJsonObject(draft.filtersJson, "trigger filters"),
  };
  if (draft.triggerType === "one_time" || draft.triggerType === "recurring") {
    trigger.at = draft.at.trim();
    trigger.missed_schedule_policy = draft.missedSchedulePolicy;
  }
  if (draft.triggerType === "recurring") trigger.interval_seconds = Number(draft.intervalSeconds);
  if (draft.triggerType === "platform_event") trigger.event_type = draft.eventType.trim();
  if (draft.triggerType === "webhook") {
    trigger.webhook_source = draft.webhookSource.trim();
    const verificationRef = optional(draft.verificationRef);
    if (verificationRef) trigger.verification_ref = verificationRef;
  }
  return trigger;
}

function parseJsonObject(value: string, label: string): Record<string, JsonValue> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, JsonValue>;
}

function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function triggerSummary(automation: CanonicalAutomation): string {
  const trigger = automation.trigger;
  if (trigger.type === "recurring") return `recurring · ${trigger.interval_seconds ?? "?"}s`;
  if (trigger.type === "one_time") return `one time · ${formatDate(trigger.at)}`;
  if (trigger.type === "platform_event") return `event · ${trigger.event_type ?? "—"}`;
  if (trigger.type === "webhook") return `webhook · ${trigger.webhook_source ?? "—"}`;
  return "manual";
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
