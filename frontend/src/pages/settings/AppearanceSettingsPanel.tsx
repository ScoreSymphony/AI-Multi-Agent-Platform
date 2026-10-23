import { useMemo, useState } from "react";
import { navigation } from "../../app/navigation";
import type { FrontendPreferencesClient } from "../../api/frontendPreferences";
import type { JsonValue } from "../../api/types";
import { useFrontendCustomization } from "../../customization/FrontendCustomizationProvider";
import {
  DASHBOARD_WIDGETS,
  DEFAULT_FRONTEND_CUSTOMIZATION,
  MAX_BRANDING_IMAGE_BYTES,
  navigationPathCanBeHidden,
  normalizeFrontendCustomization,
  serializeFrontendCustomization,
  type DashboardWidgetId,
  type FrontendAppearance,
  type FrontendCustomization,
} from "../../customization/model";
import { Card } from "../../components/States";

const COLOR_FIELDS: ReadonlyArray<readonly [keyof FrontendAppearance, string]> = [
  ["background", "Background"],
  ["backgroundHighlight", "Background highlight"],
  ["surface", "Surface"],
  ["surfaceSecondary", "Secondary surface"],
  ["sidebar", "Navigation"],
  ["text", "Text"],
  ["muted", "Muted text"],
  ["accent", "Accent"],
  ["accentStrong", "Strong accent"],
];

const DASHBOARD_WIDGET_LABELS: Record<DashboardWidgetId, string> = {
  metrics: "Status metrics",
  "recent-tasks": "Recent tasks",
  "recent-runs": "Recent runs",
};

function canonicalNavigationOrder(): string[] {
  return navigation.map((item) => item.path);
}

function readImageDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Unable to read the selected image."));
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("Unable to read the selected image."));
    };
    reader.readAsDataURL(file);
  });
}

export function AppearanceSettingsPanel({
  preferencesClient,
}: {
  preferencesClient?: FrontendPreferencesClient;
}) {
  const {
    customization,
    draft,
    hasPendingChanges,
    setDraft,
    save,
    discardPreview,
    resetToDefaults,
    importJson,
    exportJson,
    hydrateSaved,
  } = useFrontendCustomization();
  const [transferText, setTransferText] = useState("");
  const [transferError, setTransferError] = useState<string | null>(null);
  const [brandingError, setBrandingError] = useState<string | null>(null);
  const [persistenceError, setPersistenceError] = useState<string | null>(null);
  const [persisting, setPersisting] = useState(false);

  const orderedNavigation = useMemo(() => {
    const order = draft.navigation.order.length > 0
      ? draft.navigation.order
      : canonicalNavigationOrder();
    const positions = new Map(order.map((path, index) => [path, index]));
    return navigation.slice().sort((left, right) =>
      (positions.get(left.path) ?? Number.MAX_SAFE_INTEGER) -
      (positions.get(right.path) ?? Number.MAX_SAFE_INTEGER)
    );
  }, [draft.navigation.order]);

  const orderedDashboardWidgets = useMemo(() => {
    const positions = new Map(draft.dashboard.order.map((widget, index) => [widget, index]));
    return DASHBOARD_WIDGETS.slice().sort((left, right) =>
      (positions.get(left) ?? Number.MAX_SAFE_INTEGER) -
      (positions.get(right) ?? Number.MAX_SAFE_INTEGER)
    );
  }, [draft.dashboard.order]);

  function update(updater: (current: FrontendCustomization) => FrontendCustomization) {
    setDraft((current) => normalizeFrontendCustomization(updater(current)));
  }

  function updateAppearance<K extends keyof FrontendAppearance>(key: K, value: FrontendAppearance[K]) {
    update((current) => ({
      ...current,
      appearance: { ...current.appearance, [key]: value },
    }));
  }

  function moveNavigation(path: string, direction: -1 | 1) {
    const order = orderedNavigation.map((item) => item.path);
    const index = order.indexOf(path);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    update((current) => ({
      ...current,
      navigation: { ...current.navigation, order },
    }));
  }

  function setNavigationVisible(path: string, visible: boolean) {
    const hidden = new Set(draft.navigation.hiddenPaths);
    if (visible) hidden.delete(path);
    else hidden.add(path);
    update((current) => ({
      ...current,
      navigation: { ...current.navigation, hiddenPaths: Array.from(hidden) },
    }));
  }

  function moveDashboardWidget(widget: DashboardWidgetId, direction: -1 | 1) {
    const order = orderedDashboardWidgets.slice();
    const index = order.indexOf(widget);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    update((current) => ({
      ...current,
      dashboard: { ...current.dashboard, order },
    }));
  }

  function setDashboardWidgetVisible(widget: DashboardWidgetId, visible: boolean) {
    const hidden = new Set(draft.dashboard.hiddenWidgets);
    if (visible) hidden.delete(widget);
    else hidden.add(widget);
    update((current) => ({
      ...current,
      dashboard: { ...current.dashboard, hiddenWidgets: Array.from(hidden) },
    }));
  }

  async function setBrandLogo(file: File | undefined) {
    if (!file) return;
    setBrandingError(null);
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setBrandingError("Logo must be a PNG, JPEG or WebP image.");
      return;
    }
    if (file.size > MAX_BRANDING_IMAGE_BYTES) {
      setBrandingError(`Logo must be at most ${Math.floor(MAX_BRANDING_IMAGE_BYTES / 1024)} KiB.`);
      return;
    }
    try {
      const dataUrl = await readImageDataUrl(file);
      update((current) => ({
        ...current,
        branding: { ...current.branding, logoDataUrl: dataUrl },
      }));
    } catch (error) {
      setBrandingError(error instanceof Error ? error.message : "Unable to read the selected logo.");
    }
  }

  async function persistDraft() {
    setPersistenceError(null);
    setPersisting(true);
    try {
      if (!preferencesClient) {
        save();
        return;
      }
      const current = await preferencesClient.preference();
      const payload = JSON.parse(
        serializeFrontendCustomization(draft),
      ) as Record<string, JsonValue>;
      const persisted = await preferencesClient.update(current.id, payload, current.revision);
      if (persisted.customization === null) {
        throw new Error("Canonical frontend preference returned no customization after save.");
      }
      hydrateSaved(normalizeFrontendCustomization(persisted.customization));
    } catch (error) {
      setPersistenceError(
        error instanceof Error ? error.message : "Unable to save frontend customization.",
      );
    } finally {
      setPersisting(false);
    }
  }

  async function resetPersisted() {
    if (!window.confirm("Reset all appearance, layout, branding and navigation preferences?")) {
      return;
    }
    setPersistenceError(null);
    setPersisting(true);
    try {
      if (preferencesClient) {
        const current = await preferencesClient.preference();
        await preferencesClient.reset(current.id, current.revision);
      }
      resetToDefaults();
    } catch (error) {
      setPersistenceError(
        error instanceof Error ? error.message : "Unable to reset frontend customization.",
      );
    } finally {
      setPersisting(false);
    }
  }

  return (
    <Card title="Appearance & layout">
      <div className="stack customization-panel">
        <div>
          <p>
            Preview visual changes immediately, then save them when the result is right. Reset always
            restores the built-in platform design.
          </p>
          <div className="customization-savebar">
            <span role="status" aria-live="polite">
              {hasPendingChanges ? "Preview has unsaved changes." : "Saved appearance is active."}
            </span>
            <div className="actions">
              <button disabled={!hasPendingChanges || persisting} onClick={discardPreview}>
                Discard preview
              </button>
              <button
                className="primary"
                disabled={!hasPendingChanges || persisting}
                onClick={() => void persistDraft()}
              >
                {persisting ? "Saving…" : "Save appearance"}
              </button>
              <button disabled={persisting} onClick={() => void resetPersisted()}>
                Reset defaults
              </button>
            </div>
          </div>
          {persistenceError ? (
            <p className="customization-transfer-error" role="alert">{persistenceError}</p>
          ) : null}
        </div>

        <section className="customization-section" aria-labelledby="customization-colors">
          <h3 id="customization-colors">Colors</h3>
          <div className="customization-color-grid">
            {COLOR_FIELDS.map(([key, label]) => (
              <label className="customization-color-field" key={key}>
                <span>{label}</span>
                <div>
                  <input
                    aria-label={`${label} color`}
                    type="color"
                    value={String(draft.appearance[key])}
                    onChange={(event) => updateAppearance(key, event.target.value as FrontendAppearance[typeof key])}
                  />
                  <code>{String(draft.appearance[key])}</code>
                </div>
              </label>
            ))}
          </div>
        </section>

        <section className="customization-section" aria-labelledby="customization-shape">
          <h3 id="customization-shape">Typography, density & shape</h3>
          <div className="form-grid">
            <label>
              Font family
              <select
                value={draft.appearance.fontPreset}
                onChange={(event) => updateAppearance(
                  "fontPreset",
                  event.target.value as FrontendAppearance["fontPreset"],
                )}
              >
                <option value="system">System sans</option>
                <option value="serif">Serif</option>
                <option value="monospace">Monospace</option>
              </select>
            </label>
            <label>
              Font scale
              <input
                type="number"
                min="0.85"
                max="1.25"
                step="0.05"
                value={draft.appearance.fontScale}
                onChange={(event) => updateAppearance("fontScale", Number(event.target.value))}
              />
            </label>
            <label>
              Density
              <select
                value={draft.appearance.density}
                onChange={(event) => updateAppearance("density", event.target.value as FrontendAppearance["density"])}
              >
                <option value="compact">Compact</option>
                <option value="comfortable">Comfortable</option>
                <option value="spacious">Spacious</option>
              </select>
            </label>
            <label>
              Control radius (px)
              <input
                type="number"
                min="0"
                max="20"
                value={draft.appearance.controlRadius}
                onChange={(event) => updateAppearance("controlRadius", Number(event.target.value))}
              />
            </label>
            <label>
              Card radius (px)
              <input
                type="number"
                min="0"
                max="28"
                value={draft.appearance.cardRadius}
                onChange={(event) => updateAppearance("cardRadius", Number(event.target.value))}
              />
            </label>
            <label>
              Card shadow
              <select
                value={draft.appearance.shadow}
                onChange={(event) => updateAppearance("shadow", event.target.value as FrontendAppearance["shadow"])}
              >
                <option value="none">None</option>
                <option value="soft">Soft</option>
                <option value="medium">Medium</option>
              </select>
            </label>
            <label>
              Content max width (px)
              <input
                type="number"
                min="960"
                max="2200"
                step="20"
                value={draft.layout.maxContentWidth}
                onChange={(event) => update((current) => ({
                  ...current,
                  layout: { ...current.layout, maxContentWidth: Number(event.target.value) },
                }))}
              />
            </label>
          </div>
        </section>

        <section className="customization-section" aria-labelledby="customization-branding">
          <h3 id="customization-branding">Branding</h3>
          <div className="form-grid">
            <label>
              Application name
              <input
                value={draft.branding.appName}
                maxLength={80}
                onChange={(event) => update((current) => ({
                  ...current,
                  branding: { ...current.branding, appName: event.target.value },
                }))}
              />
            </label>
            <label>
              Navigation subtitle
              <input
                value={draft.branding.subtitle}
                maxLength={80}
                onChange={(event) => update((current) => ({
                  ...current,
                  branding: { ...current.branding, subtitle: event.target.value },
                }))}
              />
            </label>
          </div>
          <div className="customization-branding-row">
            {draft.branding.logoDataUrl ? (
              <img className="customization-logo-preview" src={draft.branding.logoDataUrl} alt="Custom brand preview" />
            ) : (
              <div className="customization-logo-placeholder">Default platform icon</div>
            )}
            <div className="stack">
              <label>
                Custom logo / favicon
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    void setBrandLogo(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              <small>
                PNG, JPEG or WebP; maximum {Math.floor(MAX_BRANDING_IMAGE_BYTES / 1024)} KiB.
                SVG and remote image URLs are intentionally not accepted.
              </small>
              <div className="actions">
                <button
                  disabled={!draft.branding.logoDataUrl}
                  onClick={() => update((current) => ({
                    ...current,
                    branding: { ...current.branding, logoDataUrl: null },
                  }))}
                >
                  Use default icon
                </button>
              </div>
            </div>
          </div>
          {brandingError ? <p className="customization-transfer-error" role="alert">{brandingError}</p> : null}
        </section>

        <section className="customization-section" aria-labelledby="customization-navigation">
          <h3 id="customization-navigation">Navigation</h3>
          <div className="form-grid">
            <label>
              Position
              <select
                value={draft.navigation.position}
                onChange={(event) => update((current) => ({
                  ...current,
                  navigation: {
                    ...current.navigation,
                    position: event.target.value as FrontendCustomization["navigation"]["position"],
                  },
                }))}
              >
                <option value="left">Left</option>
                <option value="right">Right</option>
              </select>
            </label>
            <label>
              Width (px)
              <input
                type="number"
                min="220"
                max="420"
                value={draft.navigation.width}
                onChange={(event) => update((current) => ({
                  ...current,
                  navigation: { ...current.navigation, width: Number(event.target.value) },
                }))}
              />
            </label>
            <label className="customization-checkbox-field">
              <input
                type="checkbox"
                checked={draft.navigation.collapsed}
                onChange={(event) => update((current) => ({
                  ...current,
                  navigation: { ...current.navigation, collapsed: event.target.checked },
                }))}
              />
              Start with navigation collapsed
            </label>
          </div>
          <div className="customization-navigation-list">
            {orderedNavigation.map((item, index) => {
              const canHide = navigationPathCanBeHidden(item.path);
              const visible = canHide ? !draft.navigation.hiddenPaths.includes(item.path) : true;
              return (
                <div className="customization-navigation-row" key={item.path}>
                  <label>
                    <input
                      type="checkbox"
                      checked={visible}
                      disabled={!canHide}
                      onChange={(event) => setNavigationVisible(item.path, event.target.checked)}
                    />
                    <span>{item.label}</span>
                    <code>{item.path}</code>
                  </label>
                  <div className="actions">
                    <button
                      aria-label={`Move ${item.label} up`}
                      disabled={index === 0}
                      onClick={() => moveNavigation(item.path, -1)}
                    >
                      Up
                    </button>
                    <button
                      aria-label={`Move ${item.label} down`}
                      disabled={index === orderedNavigation.length - 1}
                      onClick={() => moveNavigation(item.path, 1)}
                    >
                      Down
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="customization-note">
            Home and Settings stay visible so the customized shell always retains a recovery path.
            Collapsing the navigation exposes it through the persistent Menu control.
          </p>
        </section>

        <section className="customization-section" aria-labelledby="customization-dashboard">
          <h3 id="customization-dashboard">Dashboard</h3>
          <p>Choose which overview widgets are visible and in which order they appear.</p>
          <div className="customization-navigation-list">
            {orderedDashboardWidgets.map((widget, index) => {
              const visible = !draft.dashboard.hiddenWidgets.includes(widget);
              const label = DASHBOARD_WIDGET_LABELS[widget];
              return (
                <div className="customization-navigation-row" key={widget}>
                  <label>
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={(event) => setDashboardWidgetVisible(widget, event.target.checked)}
                    />
                    <span>{label}</span>
                  </label>
                  <div className="actions">
                    <button
                      aria-label={`Move ${label} up`}
                      disabled={index === 0}
                      onClick={() => moveDashboardWidget(widget, -1)}
                    >
                      Up
                    </button>
                    <button
                      aria-label={`Move ${label} down`}
                      disabled={index === orderedDashboardWidgets.length - 1}
                      onClick={() => moveDashboardWidget(widget, 1)}
                    >
                      Down
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="customization-section" aria-labelledby="customization-transfer">
          <h3 id="customization-transfer">Import / export</h3>
          <p>
            The versioned JSON format can be copied between browsers or kept as a reusable theme preset.
          </p>
          <textarea
            className="customization-json"
            aria-label="Customization JSON"
            rows={12}
            value={transferText}
            onChange={(event) => {
              setTransferText(event.target.value);
              setTransferError(null);
            }}
          />
          {transferError ? <p className="customization-transfer-error" role="alert">{transferError}</p> : null}
          <div className="actions">
            <button
              onClick={() => {
                setTransferText(exportJson());
                setTransferError(null);
              }}
            >
              Export saved theme
            </button>
            <button
              disabled={!transferText.trim()}
              onClick={() => {
                try {
                  importJson(transferText);
                  setTransferError(null);
                } catch (error) {
                  setTransferError(error instanceof Error ? error.message : "Unable to import customization.");
                }
              }}
            >
              Preview imported theme
            </button>
            <button
              onClick={() => {
                setTransferText(JSON.stringify(DEFAULT_FRONTEND_CUSTOMIZATION, null, 2));
                setTransferError(null);
              }}
            >
              Load default JSON
            </button>
          </div>
        </section>

        <p className="customization-note">
          Saved theme: <strong>{customization.branding.appName}</strong>. Appearance settings are
          personal presentation state only; hiding a navigation entry never changes authorization or
          disables the underlying platform capability.
        </p>
      </div>
    </Card>
  );
}
