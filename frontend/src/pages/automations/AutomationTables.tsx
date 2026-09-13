import type { CanonicalAutomation, CanonicalAutomationDelivery } from "../../api/automations";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";
import { formatAutomationDate, triggerSummary } from "./automationPresentation";

export function AutomationTable({ automations }: { automations: CanonicalAutomation[] }) {
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
              <td>{formatAutomationDate(automation.next_evaluation_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DeliveryTable({
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
              <td>{formatAutomationDate(delivery.received_at)}</td>
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
