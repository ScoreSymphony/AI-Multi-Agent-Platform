import { useCallback, useEffect, useState } from "react";
import { AutomationClient, type CanonicalAutomation } from "../../api/automations";
import { ControlPlaneCollectionClient } from "../../api/collections";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { PaginationControls } from "../../components/Pagination";
import { Card, ErrorState, LoadingState } from "../../components/States";
import { AutomationForm } from "./AutomationForm";
import { AutomationTable } from "./AutomationTables";
import { toCreateInput, type AutomationDraft } from "./automationDraft";

const AUTOMATION_COLLECTION = "automations";

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
        <AutomationInventoryState page={page} error={error} onRetry={() => void load()} />
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

export function AutomationInventoryState({
  page,
  error,
  onRetry,
}: {
  page: Page<CanonicalAutomation> | null;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <>
      {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {!page && !error ? <LoadingState /> : null}
      {page ? <AutomationTable automations={page.items} /> : null}
    </>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
