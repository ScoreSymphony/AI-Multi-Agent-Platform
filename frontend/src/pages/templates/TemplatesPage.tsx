import { useCallback, useEffect, useState } from "react";
import { type CanonicalTemplate, TemplateClient } from "../../api/templates";
import type { Page } from "../../api/types";
import { useCursorPagination } from "../../app/pagination";
import { useRouter } from "../../app/router";
import { PaginationControls } from "../../components/Pagination";
import { Card, ErrorState, LoadingState } from "../../components/States";
import { ExistingTemplateForm, TemplateJsonForm } from "./TemplateCreationForms";
import { TemplateTable } from "./TemplatePresentation";
import {
  createTemplateFromExisting,
  parseTemplateContent,
  type ExistingTemplateDraft,
} from "./templateCreation";

const TEMPLATE_QUERY_KEY = "templates:updated";

export function TemplateLibraryState({
  templates,
  error,
  onRetry,
}: {
  templates: Page<CanonicalTemplate> | null;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <>
      {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {!templates ? <LoadingState label="Loading Templates…" /> : <TemplateTable templates={templates.items} />}
    </>
  );
}

export function TemplatesPage({ client }: { client: TemplateClient }) {
  const { navigate } = useRouter();
  const [templates, setTemplates] = useState<Page<CanonicalTemplate> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const pagination = useCursorPagination(TEMPLATE_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      setTemplates(await client.listTemplates({ limit: 50, cursor: pagination.cursor }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor]);

  useEffect(() => void load(), [load]);

  const createFromExisting = async (draft: ExistingTemplateDraft) => {
    setBusy(true);
    setActionError(null);
    try {
      const created = await createTemplateFromExisting(client, draft);
      navigate(`/templates/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const createFromJson = async (contentJson: string) => {
    setBusy(true);
    setActionError(null);
    try {
      const created = await client.create(parseTemplateContent(contentJson));
      navigate(`/templates/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Reusable configuration</p>
        <h1>Templates</h1>
        <p>
          Versioned configuration intent for canonical platform resources. Preview resolves
          dependencies, compatibility and privileges on the server before anything is created.
        </p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Template library">
        <TemplateLibraryState templates={templates} error={error} onRetry={() => void load()} />
        {templates ? (
          <PaginationControls
            page={templates}
            pageNumber={pagination.pageNumber}
            hasPrevious={pagination.hasPrevious}
            onPrevious={pagination.previous}
            onRefresh={() => void load()}
            onNext={() => pagination.next(templates.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Create from an existing canonical resource">
        <ExistingTemplateForm disabled={busy} onSubmit={createFromExisting} />
      </Card>

      <Card title="Create from canonical Template JSON">
        <p>
          Advanced surface for composite or future Template types. Plaintext secrets and
          runtime-private fields are still rejected by the server.
        </p>
        <TemplateJsonForm disabled={busy} onSubmit={createFromJson} />
      </Card>
    </div>
  );
}
