import { useEffect, useMemo, useState, type ReactNode } from "react";

export interface ResourceOption {
  value: string;
  label: string;
  description?: string;
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="configuration-field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function CheckboxField({
  label,
  checked,
  onChange,
  hint,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className="configuration-checkbox">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
      <span>
        <strong>{label}</strong>
        {hint ? <small>{hint}</small> : null}
      </span>
    </label>
  );
}

export function ResourcePicker({
  label,
  value,
  options,
  onChange,
  allowEmpty = true,
  emptyLabel = "Use default / none",
  hint,
  disabled = false,
}: {
  label: string;
  value: string | null;
  options: ResourceOption[];
  onChange: (value: string | null) => void;
  allowEmpty?: boolean;
  emptyLabel?: string;
  hint?: string;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = useMemo(
    () => options.filter((option) => {
      if (!normalizedQuery) return true;
      return `${option.label} ${option.value} ${option.description ?? ""}`
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    }),
    [normalizedQuery, options],
  );
  const selectedMissing = value !== null && !options.some((option) => option.value === value);

  return (
    <div className="configuration-field">
      <span>{label}</span>
      {options.length > 8 ? (
        <input
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.currentTarget.value)}
          placeholder={`Filter ${label.toLocaleLowerCase()}…`}
          aria-label={`Filter ${label}`}
        />
      ) : null}
      <select
        value={value ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value || null)}
      >
        {allowEmpty ? <option value="">{emptyLabel}</option> : null}
        {selectedMissing ? <option value={value ?? ""}>{value} (currently referenced)</option> : null}
        {filtered.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}{option.description ? ` — ${option.description}` : ""}
          </option>
        ))}
      </select>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

export function MultiResourcePicker({
  label,
  values,
  options,
  onChange,
  hint,
}: {
  label: string;
  values: string[];
  options: ResourceOption[];
  onChange: (values: string[]) => void;
  hint?: string;
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = options.filter((option) => {
    if (!normalizedQuery) return true;
    return `${option.label} ${option.value} ${option.description ?? ""}`
      .toLocaleLowerCase()
      .includes(normalizedQuery);
  });
  const missing = values.filter((value) => !options.some((option) => option.value === value));

  const toggle = (value: string, checked: boolean) => {
    const next = checked
      ? [...new Set([...values, value])]
      : values.filter((item) => item !== value);
    onChange(next);
  };

  return (
    <fieldset className="configuration-multi-picker">
      <legend>{label}</legend>
      <input
        value={query}
        onChange={(event) => setQuery(event.currentTarget.value)}
        placeholder={`Filter ${label.toLocaleLowerCase()}…`}
        aria-label={`Filter ${label}`}
      />
      <div className="configuration-option-list">
        {missing.map((value) => (
          <label key={`missing:${value}`} className="configuration-option missing-reference">
            <input type="checkbox" checked onChange={(event) => toggle(value, event.currentTarget.checked)} />
            <span><strong>{value}</strong><small>Currently referenced; not present in inventory</small></span>
          </label>
        ))}
        {filtered.map((option) => (
          <label key={option.value} className="configuration-option">
            <input
              type="checkbox"
              checked={values.includes(option.value)}
              onChange={(event) => toggle(option.value, event.currentTarget.checked)}
            />
            <span>
              <strong>{option.label}</strong>
              <small>{option.description ?? option.value}</small>
            </span>
          </label>
        ))}
        {filtered.length === 0 && missing.length === 0 ? <small>No matching resources.</small> : null}
      </div>
      {hint ? <small>{hint}</small> : null}
    </fieldset>
  );
}

export function StringListField({
  label,
  values,
  onChange,
  hint,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        value={values.join(", ")}
        onChange={(event) => onChange(splitList(event.currentTarget.value))}
        placeholder="Comma-separated values"
      />
    </Field>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min = 1,
  hint,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type="number"
        min={min}
        value={value ?? ""}
        onChange={(event) => {
          const raw = event.currentTarget.value;
          onChange(raw === "" ? null : Number(raw));
        }}
      />
    </Field>
  );
}

export function ConfigurationBar({
  dirty,
  busy,
  onSave,
  onDiscard,
  saveLabel = "Save configuration",
}: {
  dirty: boolean;
  busy: boolean;
  onSave: () => void;
  onDiscard: () => void;
  saveLabel?: string;
}) {
  return (
    <div className="configuration-bar" role="region" aria-label="Configuration actions">
      <span>{dirty ? "Unsaved changes" : "Configuration matches the loaded revision"}</span>
      <div className="actions">
        <button type="button" disabled={!dirty || busy} onClick={onDiscard}>Discard</button>
        <button type="button" className="primary" disabled={!dirty || busy} onClick={onSave}>
          {busy ? "Saving…" : saveLabel}
        </button>
      </div>
    </div>
  );
}

export function useUnsavedChanges(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return undefined;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const interceptNavigation = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      if (!window.confirm("Discard unsaved configuration changes?")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", interceptNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", interceptNavigation, true);
    };
  }, [dirty]);
}

export function configurationFingerprint(value: unknown): string {
  return JSON.stringify(value);
}

function splitList(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}
