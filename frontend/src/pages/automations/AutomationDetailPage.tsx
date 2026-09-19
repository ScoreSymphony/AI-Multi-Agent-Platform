import { useCallback, useEffect, useState } from "react";
import {
  AutomationClient,
  type AutomationState,
  type CanonicalAutomation,
  type CanonicalAutomationDelivery,
} from "../../api/automations";
import { ControlPlaneCollectionClient } from "../../api/collections";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { PaginationControls } from "../../components/Pagination";
import {
  Card,
  CanonicalId,
  DegradedState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../../components/States";
import { AutomationForm } from "./AutomationForm";
import { DeliveryTable } from "./AutomationTables";
import {
  parseJsonObject,
  toUpdateInput,
  type AutomationDraft,
} from "./automationDraft";
import { formatAutomationDate } from "./automationPresentation";

const AUTOMATION_COLLECTION = "automations";
const DELIVERY_COLLECTION = "automation-deliveries";

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
      try {
        await loadAutomation();
      } catch (refreshError) {
        setError(refreshError);
      }
    } finally {
      setBusy(false);
    }
  }

  async function update(draft: AutomationDraft) {
    await runAction(() => automations.update(automationId, toUpdateInput(draft)));
  }

  async function disable() {
    if (!window.confirm(`Disable automation ${automationId}? Scheduled and event-driven delivery will stop until it is explicitly enabled again.`)) return;
    await runAction(() => automations.disable(automationId));
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
        {automation.state === "invalid" ? (
          <DegradedState
            title="Automation configuration is invalid"
            detail={`Reason: ${automation.invalidation_reason_code ?? "unspecified"}. Revalidation is a canonical administrative lifecycle operation; the browser does not repair or replace configuration locally.`}
          />
        ) : null}
        <div className="actions">
          {automationLifecycleActions(automation.state).includes("pause") ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.pause(automation.id))}>
              Pause
            </button>
          ) : null}
          {automationLifecycleActions(automation.state).includes("resume") ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.resume(automation.id))}>
              {automation.state === "disabled" ? "Enable" : "Resume"}
            </button>
          ) : null}
          {automationLifecycleActions(automation.state).includes("disable") ? (
            <button disabled={busy} onClick={() => void disable()}>
              Disable
            </button>
          ) : null}
          {automationLifecycleActions(automation.state).includes("revalidate") ? (
            <button disabled={busy} onClick={() => void runAction(() => automations.revalidate(automation.id))}>
              Revalidate
            </button>
          ) : null}
          <button disabled={busy} onClick={() => void load()}>Refresh</button>
        </div>
        <p>
          State transitions go through canonical #18 commands and remain subject to server-side
          authorization. Disable is reversible through canonical resume/enable semantics; no
          automation.delete command exists, so the Web UI does not invent destructive deletion.
        </p>
      </Card>

      <div className="grid-two">
        <Card title="Trigger">
          <DefinitionList
            values={{
              type: automation.trigger.type,
              timezone: automation.trigger.timezone,
              at: formatAutomationDate(automation.trigger.at),
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
              last_evaluated: formatAutomationDate(automation.last_evaluated_at),
              next_evaluation: formatAutomationDate(automation.next_evaluation_at),
              invalidated_at: formatAutomationDate(automation.invalidated_at ?? null),
              state_before_invalid: automation.state_before_invalid ?? "—",
            }}
          />
        </Card>
        <Card title="Canonical scope">
          <DefinitionList
            values={{
              project: automation.project_id ?? "—",
              workspace: automation.workspace_id ?? "—",
              principal: automation.identity.principal_ref,
              created: formatAutomationDate(automation.created_at),
              updated: formatAutomationDate(automation.updated_at),
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

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return (
    <dl className="definition-list">
      {Object.entries(values).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}


export type AutomationLifecycleAction = "pause" | "resume" | "disable" | "revalidate";

export function automationLifecycleActions(state: AutomationState): AutomationLifecycleAction[] {
  if (state === "enabled") return ["pause", "disable"];
  if (state === "paused") return ["resume", "disable"];
  if (state === "disabled") return ["resume"];
  return ["revalidate"];
}
