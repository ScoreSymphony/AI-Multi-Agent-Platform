import { useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  AutomationOverlapPolicy,
  AutomationTriggerType,
  CanonicalAutomation,
  MissedSchedulePolicy,
} from "../../api/automations";
import {
  draftFromAutomation,
  validateAutomationDraft,
  type AutomationDraft,
} from "./automationDraft";

export function AutomationForm({
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
      validateAutomationDraft(draft);
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
