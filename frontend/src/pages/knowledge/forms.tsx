import { useState, type FormEvent } from "react";
import type { CanonicalKnowledgeSource } from "../../api/memoryKnowledge";
import type { JsonValue } from "../../api/types";
import { parseJsonObject } from "../memoryKnowledge/input";

export interface KnowledgeRegisterDraft {
  targetRef: string;
  title: string;
  revision: string;
  projectId: string;
  metadataJson: string;
}

export function KnowledgeRegisterForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (draft: KnowledgeRegisterDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<KnowledgeRegisterDraft>({
    targetRef: "",
    title: "",
    revision: "1",
    projectId: "",
    metadataJson: "{}",
  });
  const set = (key: keyof KnowledgeRegisterDraft, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(draft);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Target ref</span><input required value={draft.targetRef} onChange={(event) => set("targetRef", event.target.value)} /></label>
      <label className="field"><span>Title</span><input required value={draft.title} onChange={(event) => set("title", event.target.value)} /></label>
      <label className="field"><span>Revision</span><input value={draft.revision} onChange={(event) => set("revision", event.target.value)} /></label>
      <label className="field"><span>Project ID (optional)</span><input value={draft.projectId} onChange={(event) => set("projectId", event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={draft.metadataJson} onChange={(event) => set("metadataJson", event.target.value)} /></label>
      <button disabled={disabled} type="submit">Register source</button>
    </form>
  );
}

export function KnowledgeUpdateForm({
  source,
  disabled,
  onSubmit,
}: {
  source: CanonicalKnowledgeSource;
  disabled: boolean;
  onSubmit: (title: string, metadata: Record<string, JsonValue>) => Promise<void>;
}) {
  const [title, setTitle] = useState(source.title);
  const [metadataJson, setMetadataJson] = useState(JSON.stringify(source.metadata, null, 2));
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(title.trim(), parseJsonObject(metadataJson, "Knowledge metadata"));
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Title</span><input required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="field field-wide"><span>Metadata JSON object</span><textarea rows={5} value={metadataJson} onChange={(event) => setMetadataJson(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Update metadata</button>
    </form>
  );
}

export function KnowledgeIngestForm({
  disabled,
  submitLabel,
  onSubmit,
}: {
  disabled: boolean;
  submitLabel: string;
  onSubmit: (content: string, location: string) => Promise<void>;
}) {
  const [content, setContent] = useState("");
  const [location, setLocation] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(content, location);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>Canonical source location</span><input required value={location} onChange={(event) => setLocation(event.target.value)} /></label>
      <label className="field field-wide"><span>Content</span><textarea rows={8} required value={content} onChange={(event) => setContent(event.target.value)} /></label>
      <button disabled={disabled} type="submit">{submitLabel}</button>
    </form>
  );
}

export function KnowledgeReindexForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (revision: string, content: string, location: string) => Promise<void>;
}) {
  const [revision, setRevision] = useState("");
  const [content, setContent] = useState("");
  const [location, setLocation] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSubmit(revision, content, location);
  }
  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="field"><span>New source revision</span><input required value={revision} onChange={(event) => setRevision(event.target.value)} /></label>
      <label className="field"><span>Canonical source location</span><input required value={location} onChange={(event) => setLocation(event.target.value)} /></label>
      <label className="field field-wide"><span>Content</span><textarea rows={8} required value={content} onChange={(event) => setContent(event.target.value)} /></label>
      <button disabled={disabled} type="submit">Re-index source</button>
    </form>
  );
}
