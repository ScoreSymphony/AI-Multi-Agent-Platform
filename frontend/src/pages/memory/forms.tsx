import { useState, type FormEvent } from "react";
import type {
  CanonicalMemoryEntry,
  MemoryOrigin,
  MemoryRetention,
  MemoryScope,
  MemoryType,
} from "../../api/memoryKnowledge";
import {
  DURABLE_MEMORY_SCOPES,
  MEMORY_ORIGINS,
  MEMORY_RETENTIONS,
  MEMORY_SCOPES,
  SEMANTIC_MEMORY_TYPES,
} from "./constants";

export interface MemoryCreateDraft {
  scope: MemoryScope;
  scopeId: string;
  origin: MemoryOrigin;
  memoryType: MemoryType | "";
  valueJson: string;
  retention: MemoryRetention | "";
  expiresAt: string;
  projectId: string;
  classification: string;
  metadataJson: string;
}

export function MemoryCreateForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (draft: MemoryCreateDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<MemoryCreateDraft>({
    scope: "user",
    scopeId: "",
    origin: "user-authored",
    memoryType: "",
    valueJson: "{}",
    retention: "",
    expiresAt: "",
    projectId: "",
    classification: "",
    metadataJson: "{}",
  });
  const set = (key: keyof MemoryCreateDraft, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(draft);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Scope</span><select value={draft.scope} onChange={(event) => set("scope", event.target.value)}>{MEMORY_SCOPES.map((scope) => <option key={scope} value={scope}>{scope}</option>)}</select></label>
      <label className="field"><span>Scope ID</span><input required value={draft.scopeId} onChange={(event) => set("scopeId", event.target.value)} /></label>
      <label className="field"><span>Origin</span><select value={draft.origin} onChange={(event) => set("origin", event.target.value)}>{MEMORY_ORIGINS.map((origin) => <option key={origin} value={origin}>{origin}</option>)}</select></label>
      <label className="field">
        <span>Memory Type</span>
        <select required value={draft.memoryType} onChange={(event) => set("memoryType", event.target.value)}>
          <option value="" disabled>Select a Memory Type</option>
          {SEMANTIC_MEMORY_TYPES.map((memoryType) => <option key={memoryType} value={memoryType}>{memoryType}</option>)}
          <option value="unclassified">unclassified (legacy/import compatibility)</option>
        </select>
      </label>
      <label className="field"><span>Retention (optional)</span><select value={draft.retention} onChange={(event) => set("retention", event.target.value)}><option value="">server default</option>{MEMORY_RETENTIONS.map((retention) => <option key={retention} value={retention}>{retention}</option>)}</select></label>
      <label className="field"><span>Expires at ISO timestamp (optional)</span><input value={draft.expiresAt} onChange={(event) => set("expiresAt", event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={draft.projectId} onChange={(event) => set("projectId", event.target.value)} /></label>
      <label className="field"><span>Classification (optional)</span><input value={draft.classification} onChange={(event) => set("classification", event.target.value)} /></label>
      <label className="field field-wide"><span>Value JSON</span><textarea required rows={6} value={draft.valueJson} onChange={(event) => set("valueJson", event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={draft.metadataJson} onChange={(event) => set("metadataJson", event.target.value)} /></label>
      <button disabled={disabled} type="submit">Create Memory</button>
    </form>
  );
}

export function MemoryUpdateForm({
  entry,
  disabled,
  onSubmit,
}: {
  entry: CanonicalMemoryEntry;
  disabled: boolean;
  onSubmit: (valueJson: string, classification: string, metadataJson: string) => Promise<void>;
}) {
  const [valueJson, setValueJson] = useState(JSON.stringify(entry.value, null, 2));
  const [classification, setClassification] = useState(entry.classification ?? "");
  const [metadataJson, setMetadataJson] = useState(JSON.stringify(entry.metadata, null, 2));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(valueJson, classification, metadataJson);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field field-wide"><span>Replacement value JSON</span><textarea rows={6} required value={valueJson} onChange={(event) => setValueJson(event.target.value)} /></label>
      <label className="field"><span>Classification</span><input value={classification} onChange={(event) => setClassification(event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={metadataJson} onChange={(event) => setMetadataJson(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Create superseding Memory</button>
    </form>
  );
}

export function MemoryPromoteForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (
    scope: Exclude<MemoryScope, "short_term">,
    scopeId: string,
    projectId: string,
  ) => Promise<void>;
}) {
  const [scope, setScope] = useState<Exclude<MemoryScope, "short_term">>("user");
  const [scopeId, setScopeId] = useState("");
  const [projectId, setProjectId] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(scope, scopeId, projectId);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Target scope</span><select value={scope} onChange={(event) => setScope(event.target.value as Exclude<MemoryScope, "short_term">)}>{DURABLE_MEMORY_SCOPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label className="field"><span>Target scope ID</span><input required value={scopeId} onChange={(event) => setScopeId(event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Promote Memory</button>
    </form>
  );
}
