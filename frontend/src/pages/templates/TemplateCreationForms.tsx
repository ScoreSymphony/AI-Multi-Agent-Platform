import { useState, type FormEvent } from "react";
import { emptyTemplateContent } from "../../api/templates";
import type { ExistingTemplateDraft, ExportKind } from "./templateCreation";

export function ExistingTemplateForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (draft: ExistingTemplateDraft) => Promise<void>;
}) {
  const [exportKind, setExportKind] = useState<ExportKind>("agent");
  const [sourceRef, setSourceRef] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [projectTemplateRef, setProjectTemplateRef] = useState("");
  const [projectTemplateRevision, setProjectTemplateRevision] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onSubmit({ exportKind, sourceRef, templateName, projectTemplateRef, projectTemplateRevision });
  };

  return (
    <form className="stack" onSubmit={(event) => void submit(event)}>
      <label>
        Source type
        <select value={exportKind} onChange={(event) => setExportKind(event.target.value as ExportKind)}>
          <option value="agent">Agent</option>
          <option value="agent_team">Agent Team</option>
          <option value="workflow">Workflow</option>
          <option value="capability_assignment">Capability Assignment</option>
          <option value="model_routing_profile">Model Routing Profile</option>
          <option value="automation">Automation</option>
          <option value="project">Project</option>
          <option value="workspaces">Workspace structure</option>
        </select>
      </label>
      <label>
        {exportKind === "workspaces" ? "Workspace IDs (comma or line separated)" : "Canonical source ID"}
        <textarea
          rows={exportKind === "workspaces" ? 3 : 1}
          required
          value={sourceRef}
          onChange={(event) => setSourceRef(event.target.value)}
        />
      </label>
      <label>
        Template name {exportKind === "workspaces" ? "" : "(optional)"}
        <input
          required={exportKind === "workspaces"}
          value={templateName}
          onChange={(event) => setTemplateName(event.target.value)}
        />
      </label>
      {exportKind === "workspaces" ? (
        <div className="detail-grid">
          <label>
            Project Template ID (optional)
            <input value={projectTemplateRef} onChange={(event) => setProjectTemplateRef(event.target.value)} />
          </label>
          <label>
            Project Template revision (optional)
            <input
              inputMode="numeric"
              value={projectTemplateRevision}
              onChange={(event) => setProjectTemplateRevision(event.target.value)}
            />
          </label>
        </div>
      ) : null}
      <div className="button-row">
        <button disabled={disabled} type="submit">Create draft</button>
      </div>
    </form>
  );
}

export function TemplateJsonForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (contentJson: string) => Promise<void>;
}) {
  const [contentJson, setContentJson] = useState(() => JSON.stringify(emptyTemplateContent(), null, 2));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onSubmit(contentJson);
  };

  return (
    <form className="stack" onSubmit={(event) => void submit(event)}>
      <label>
        Template content
        <textarea
          rows={18}
          spellCheck={false}
          value={contentJson}
          onChange={(event) => setContentJson(event.target.value)}
        />
      </label>
      <div className="button-row">
        <button disabled={disabled} type="submit">Create draft from JSON</button>
      </div>
    </form>
  );
}
