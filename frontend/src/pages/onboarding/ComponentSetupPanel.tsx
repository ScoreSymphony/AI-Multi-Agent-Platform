import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type ComponentCategory,
  type ComponentSetupMode,
  type ComponentSetupProfile,
  type ComponentSetupStatus,
  type DiscoveredComponent,
  OnboardingClient,
} from "../../api/onboarding";
import type { APImanifest } from "../../api/types";
import { Card, ErrorState, LoadingState, StatusBadge } from "../../components/States";

const CATEGORY_LABELS: Record<ComponentCategory, string> = {
  orchestrator: "Orchestrator",
  model_provider: "Model provider",
  executor: "Executor / sandbox",
  memory_knowledge: "Memory / knowledge",
  tools_mcp: "Tools / MCP",
  storage: "Storage",
  compute: "Compute / worker target",
};

const MODE_LABELS: Record<ComponentSetupMode, string> = {
  auto: "Recommended / Auto",
  local: "Local only",
  multi_node: "Existing servers / Multi-node",
  advanced: "Advanced / Custom",
};

const CATEGORY_ORDER = Object.keys(CATEGORY_LABELS) as ComponentCategory[];
const AUTOMATIC_MODES = new Set<ComponentSetupMode>(["auto", "local", "multi_node"]);
const AUTOMATIC_COMPATIBLE_STATES = new Set(["compatible", "compatible_with_constraints"]);
const ADVANCED_COMPATIBLE_STATES = new Set([
  "compatible",
  "compatible_with_constraints",
  "experimental",
]);

export interface ComponentSetupPanelProps {
  onboarding: OnboardingClient;
  manifest: APImanifest | null;
  surface: "onboarding" | "settings";
}

export function ComponentSetupPanel({ onboarding, manifest, surface }: ComponentSetupPanelProps) {
  const available = manifest?.resources.includes("component-setup") ?? false;
  const saveAvailable = manifest?.commands.includes("onboarding.save-component-profile") ?? false;
  const selectAvailable = manifest?.commands.includes("onboarding.select-component-profile") ?? false;
  const [status, setStatus] = useState<ComponentSetupStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<ComponentSetupMode>("auto");
  const [profileId, setProfileId] = useState(surface === "onboarding" ? "first-run" : "platform-default");
  const [advancedDefaults, setAdvancedDefaults] = useState<Partial<Record<ComponentCategory, string>>>({});

  const load = useCallback(async () => {
    if (!available) {
      setStatus(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const nextStatus = await onboarding.componentSetup();
      setStatus(nextStatus);
      setError(null);
      const active = nextStatus.profiles.find((profile) => profile.profile_id === nextStatus.active_profile_id);
      if (active) {
        setMode(active.mode);
        setProfileId(active.profile_id);
        setAdvancedDefaults(active.mode === "advanced" ? active.defaults : {});
      }
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [available, onboarding]);

  useEffect(() => {
    void load();
  }, [load]);

  const previewDefaults = useMemo(() => {
    if (mode === "advanced") return compactDefaults(advancedDefaults);
    return status ? recommendComponentDefaults(status.components, mode) : {};
  }, [advancedDefaults, mode, status]);

  if (!available) {
    return (
      <Card title="Component discovery & setup profile">
        <p>
          This Control Plane does not advertise the canonical <code>component-setup</code> resource.
          Existing first-run onboarding remains available without inventing a parallel configuration path.
        </p>
      </Card>
    );
  }

  if (loading && status === null) return <LoadingState label="Discovering components and compatibility…" />;
  if (error && status === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (status === null) return <LoadingState label="Loading component setup…" />;

  const categories = CATEGORY_ORDER.filter((category) =>
    status.components.some((component) => component.category === category),
  );
  const canSave = saveAvailable
    && profileId.trim().length > 0
    && (mode !== "advanced" || Object.keys(previewDefaults).length > 0);

  async function saveProfile() {
    if (!canSave) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await onboarding.saveComponentProfile({
        profile_id: profileId.trim(),
        mode,
        ...(mode === "advanced" ? { defaults: previewDefaults } : {}),
        activate: true,
      });
      setNotice("Component profile saved and activated through the canonical Control Plane.");
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  }

  async function selectProfile(profile: ComponentSetupProfile) {
    if (!selectAvailable) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await onboarding.selectComponentProfile(profile.profile_id);
      setNotice(`Profile ${profile.profile_id} is now active.`);
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Card title={surface === "onboarding" ? "Environment & component discovery" : "Component defaults & setup profiles"}>
        <p>
          Discovery is read-only. It reports installed adapters, services, hardware-facing capabilities and
          compatibility facts without granting installation, activation, trust or secret authority.
        </p>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {notice ? <div className="state" role="status"><strong>{notice}</strong></div> : null}
        <div className="actions">
          <button className="secondary" disabled={busy || loading} onClick={() => void load()}>
            {loading ? "Refreshing discovery…" : "Refresh discovery"}
          </button>
        </div>
        <ComponentDiscoveryTable components={status.components} />
      </Card>

      <Card title="Setup mode and configuration preview">
        <p>
          Profiles are reversible configuration, not separate architectures. Automatic modes are re-resolved
          by the backend from the same discovery facts when saved; Advanced mode sends only explicit component IDs.
        </p>
        <fieldset disabled={busy} className="stack">
          <legend>Setup mode</legend>
          {status.available_setup_modes.map((item) => (
            <label key={item}>
              <input
                checked={mode === item}
                name="component-setup-mode"
                onChange={() => {
                  setMode(item);
                  if (AUTOMATIC_MODES.has(item) && profileId === "first-run") setProfileId(`first-run-${item}`);
                }}
                type="radio"
              />{" "}
              <strong>{MODE_LABELS[item]}</strong>
            </label>
          ))}
        </fieldset>

        <label>
          Profile ID
          <input
            disabled={busy}
            onChange={(event) => setProfileId(event.target.value)}
            value={profileId}
          />
        </label>

        {mode === "advanced" ? (
          <div className="grid-two">
            {categories.map((category) => (
              <label key={category}>
                {CATEGORY_LABELS[category]}
                <select
                  disabled={busy}
                  onChange={(event) => {
                    const value = event.target.value;
                    setAdvancedDefaults((current) => ({ ...current, [category]: value || undefined }));
                  }}
                  value={advancedDefaults[category] ?? ""}
                >
                  <option value="">No explicit default</option>
                  {status.components
                    .filter((component) => component.category === category)
                    .map((component) => (
                      <option
                        disabled={!ADVANCED_COMPATIBLE_STATES.has(component.compatibility.state)}
                        key={component.component_id}
                        value={component.component_id}
                      >
                        {component.display_name} — {component.compatibility.state}
                      </option>
                    ))}
                </select>
              </label>
            ))}
          </div>
        ) : null}

        <ConfigurationPreview defaults={previewDefaults} mode={mode} />
        <div className="actions">
          <button disabled={busy || !canSave} onClick={() => void saveProfile()}>
            {busy ? "Saving…" : "Save and activate profile"}
          </button>
        </div>
        {!saveAvailable ? <p>The Control Plane does not advertise profile-save authority.</p> : null}
        {mode === "advanced" && Object.keys(previewDefaults).length === 0 ? (
          <p>Advanced mode requires at least one explicit component selection.</p>
        ) : null}
      </Card>

      <Card title="Saved profiles">
        {status.profiles.length === 0 ? (
          <p>No persisted component profile exists yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Profile</th><th>Mode</th><th>Revision</th><th>Defaults</th><th>Status</th><th>Action</th></tr></thead>
              <tbody>
                {status.profiles.map((profile) => {
                  const active = profile.profile_id === status.active_profile_id;
                  return (
                    <tr key={profile.profile_id}>
                      <td><code>{profile.profile_id}</code></td>
                      <td>{MODE_LABELS[profile.mode]}</td>
                      <td>{profile.revision}</td>
                      <td>{formatDefaults(profile.defaults)}</td>
                      <td><StatusBadge value={active ? "active" : "inactive"} /></td>
                      <td>
                        <button
                          className="secondary"
                          disabled={busy || active || !selectAvailable}
                          onClick={() => void selectProfile(profile)}
                        >
                          Activate
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export function recommendComponentDefaults(
  components: DiscoveredComponent[],
  mode: Exclude<ComponentSetupMode, "advanced">,
): Partial<Record<ComponentCategory, string>> {
  const defaults: Partial<Record<ComponentCategory, string>> = {};
  for (const category of CATEGORY_ORDER) {
    const candidates = components
      .filter((component) => component.category === category)
      .filter((component) => component.lifecycle === "recommended" || component.lifecycle === "supported")
      .filter((component) => AUTOMATIC_COMPATIBLE_STATES.has(component.compatibility.state))
      .filter((component) => component.recommended_modes.length === 0 || component.recommended_modes.includes(mode))
      .sort((left, right) => {
        const lifecycle = lifecycleRank(left) - lifecycleRank(right);
        if (lifecycle !== 0) return lifecycle;
        const priority = right.priority - left.priority;
        if (priority !== 0) return priority;
        return left.component_id.localeCompare(right.component_id);
      });
    if (candidates[0]) defaults[category] = candidates[0].component_id;
  }
  return defaults;
}

function lifecycleRank(component: DiscoveredComponent): number {
  return component.lifecycle === "recommended" ? 0 : 1;
}

function compactDefaults(
  defaults: Partial<Record<ComponentCategory, string>>,
): Partial<Record<ComponentCategory, string>> {
  return Object.fromEntries(
    Object.entries(defaults).filter((entry): entry is [ComponentCategory, string] => Boolean(entry[1])),
  ) as Partial<Record<ComponentCategory, string>>;
}

function ComponentDiscoveryTable({ components }: { components: DiscoveredComponent[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Category</th><th>Component</th><th>Version</th><th>Availability</th><th>Lifecycle</th><th>Compatibility</th><th>Capabilities / blockers</th></tr>
        </thead>
        <tbody>
          {components.map((component) => (
            <tr key={`${component.category}:${component.component_id}`}>
              <td>{CATEGORY_LABELS[component.category]}</td>
              <td><strong>{component.display_name}</strong><br /><code>{component.component_id}</code></td>
              <td>{component.version ?? "unknown"}</td>
              <td><StatusBadge value={component.availability} /></td>
              <td><StatusBadge value={component.lifecycle} /></td>
              <td><StatusBadge value={component.compatibility.state} /></td>
              <td>
                {component.capabilities.length > 0 ? component.capabilities.join(", ") : "—"}
                {component.compatibility.reasons.length > 0 ? (
                  <ul>{component.compatibility.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                ) : null}
                {component.compatibility.missing_requirements.length > 0 ? (
                  <p>Missing: {component.compatibility.missing_requirements.join(", ")}</p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfigurationPreview({
  defaults,
  mode,
}: {
  defaults: Partial<Record<ComponentCategory, string>>;
  mode: ComponentSetupMode;
}) {
  const entries = CATEGORY_ORDER.flatMap((category) => {
    const componentId = defaults[category];
    return componentId ? [[category, componentId] as const] : [];
  });
  return (
    <div className="stack">
      <h3>Configuration preview</h3>
      <p>Mode: <strong>{MODE_LABELS[mode]}</strong></p>
      {entries.length === 0 ? (
        <p>No compatible default is currently derivable for this mode.</p>
      ) : (
        <dl className="definition-list">
          {entries.map(([category, componentId]) => (
            <div key={category}><dt>{CATEGORY_LABELS[category]}</dt><dd><code>{componentId}</code></dd></div>
          ))}
        </dl>
      )}
    </div>
  );
}

function formatDefaults(defaults: Partial<Record<ComponentCategory, string>>): string {
  const values = CATEGORY_ORDER.flatMap((category) => defaults[category] ? [`${CATEGORY_LABELS[category]}=${defaults[category]}`] : []);
  return values.length > 0 ? values.join("; ") : "—";
}
