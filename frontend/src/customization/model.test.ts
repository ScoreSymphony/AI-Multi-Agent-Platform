import { describe, expect, it } from "vitest";
import {
  DEFAULT_FRONTEND_CUSTOMIZATION,
  FRONTEND_CUSTOMIZATION_VERSION,
  customizationCssVariables,
  migrateFrontendCustomization,
  navigationPathCanBeHidden,
  normalizeFrontendCustomization,
  parseFrontendCustomizationJson,
  resolveCustomizedNavigation,
  resolveDashboardWidgets,
  resolveFrontendCustomizationLayers,
  serializeFrontendCustomization,
} from "./model";

describe("frontend customization model", () => {
  it("normalizes unsafe and out-of-range values back into the supported schema", () => {
    const normalized = normalizeFrontendCustomization({
      version: FRONTEND_CUSTOMIZATION_VERSION,
      appearance: {
        background: "url(javascript:alert(1))",
        accent: "#abcdef",
        fontScale: 9,
        fontPreset: "comic",
        density: "tiny",
        controlRadius: -100,
      },
      navigation: {
        position: "floating",
        width: 900,
        collapsed: "yes",
        hiddenPaths: ["/tasks", "/", "/settings", "not-a-path"],
      },
      branding: {
        appName: "Custom Platform",
        subtitle: "",
        logoDataUrl: "https://tracking.invalid/logo.png",
      },
      layout: { maxContentWidth: 4000, startPage: "https://example.invalid" },
      dashboard: {
        hiddenWidgets: ["recent-runs", "unknown"],
        order: ["recent-tasks", "metrics", "unknown"],
        widgetSizes: { metrics: "tiny", "recent-tasks": "wide" },
      },
    });

    expect(normalized.appearance.background).toBe(DEFAULT_FRONTEND_CUSTOMIZATION.appearance.background);
    expect(normalized.appearance.accent).toBe("#abcdef");
    expect(normalized.appearance.fontScale).toBe(1.25);
    expect(normalized.appearance.fontPreset).toBe("system");
    expect(normalized.appearance.density).toBe("comfortable");
    expect(normalized.appearance.controlRadius).toBe(0);
    expect(normalized.navigation.position).toBe("left");
    expect(normalized.navigation.width).toBe(420);
    expect(normalized.navigation.collapsed).toBe(false);
    expect(normalized.navigation.hiddenPaths).toEqual(["/tasks"]);
    expect(normalized.branding.appName).toBe("Custom Platform");
    expect(normalized.branding.subtitle).toBe("Control Plane");
    expect(normalized.branding.logoDataUrl).toBeNull();
    expect(normalized.layout.maxContentWidth).toBe(2200);
    expect(normalized.layout.startPage).toBe("/");
    expect(normalized.dashboard.hiddenWidgets).toEqual(["recent-runs"]);
    expect(normalized.dashboard.order).toEqual(["recent-tasks", "metrics", "recent-runs"]);
    expect(normalized.dashboard.widgetSizes).toEqual({
      metrics: "wide",
      "recent-tasks": "wide",
      "recent-runs": "normal",
    });
  });

  it("migrates v1 themes into v2 without losing existing presentation values", () => {
    const migrated = migrateFrontendCustomization({
      version: 1,
      appearance: { accent: "#112233" },
      layout: { maxContentWidth: 1200 },
      dashboard: { hiddenWidgets: ["recent-runs"] },
    });

    expect(migrated).toMatchObject({
      version: FRONTEND_CUSTOMIZATION_VERSION,
      appearance: { accent: "#112233" },
      layout: { maxContentWidth: 1200, startPage: "/" },
      dashboard: {
        hiddenWidgets: ["recent-runs"],
        widgetSizes: {
          metrics: "wide",
          "recent-tasks": "normal",
          "recent-runs": "normal",
        },
      },
    });
    expect(normalizeFrontendCustomization(migrated).appearance.accent).toBe("#112233");
  });

  it("accepts bounded embedded raster branding but rejects active or remote formats", () => {
    const png = "data:image/png;base64,iVBORw0KGgo=";
    expect(normalizeFrontendCustomization({
      ...DEFAULT_FRONTEND_CUSTOMIZATION,
      branding: { ...DEFAULT_FRONTEND_CUSTOMIZATION.branding, logoDataUrl: png },
    }).branding.logoDataUrl).toBe(png);

    for (const unsafe of [
      "https://example.invalid/logo.png",
      "data:image/svg+xml;base64,PHN2Zz4=",
      "data:text/html;base64,PGgxPk5vPC9oMT4=",
    ]) {
      expect(normalizeFrontendCustomization({
        ...DEFAULT_FRONTEND_CUSTOMIZATION,
        branding: { ...DEFAULT_FRONTEND_CUSTOMIZATION.branding, logoDataUrl: unsafe },
      }).branding.logoDataUrl).toBeNull();
    }
  });

  it("keeps Home and Settings as non-hideable recovery routes", () => {
    expect(navigationPathCanBeHidden("/")).toBe(false);
    expect(navigationPathCanBeHidden("/settings")).toBe(false);
    expect(navigationPathCanBeHidden("/tasks")).toBe(true);
  });

  it("applies custom ordering while preserving unspecified canonical items", () => {
    const items = [
      { path: "/", label: "Home" },
      { path: "/tasks", label: "Tasks" },
      { path: "/agents", label: "Agents" },
      { path: "/settings", label: "Settings" },
    ];

    const resolved = resolveCustomizedNavigation(items, {
      position: "left",
      width: 276,
      collapsed: false,
      hiddenPaths: ["/agents", "/settings"],
      order: ["/tasks", "/"],
    });

    expect(resolved.map((item) => item.path)).toEqual(["/tasks", "/", "/settings"]);
  });

  it("resolves dashboard visibility and ordering deterministically", () => {
    expect(resolveDashboardWidgets({
      hiddenWidgets: ["recent-tasks"],
      order: ["recent-runs", "metrics", "recent-tasks"],
      widgetSizes: { ...DEFAULT_FRONTEND_CUSTOMIZATION.dashboard.widgetSizes },
    })).toEqual(["recent-runs", "metrics"]);
  });

  it("merges platform, organization/workspace and personal layers from least to most specific", () => {
    const resolved = resolveFrontendCustomizationLayers(
      {
        appearance: { accent: "#111111" },
        branding: { appName: "Deployment" },
        navigation: { width: 300 },
        dashboard: { widgetSizes: { "recent-tasks": "wide" } },
      },
      {
        appearance: { accent: "#222222" },
        branding: { subtitle: "Workspace" },
        layout: { startPage: "/tasks" },
      },
      {
        appearance: { fontPreset: "serif" },
        navigation: { width: 340, collapsed: true },
        branding: { appName: "Personal" },
        dashboard: { widgetSizes: { "recent-runs": "wide" } },
      },
    );

    expect(resolved.appearance.accent).toBe("#222222");
    expect(resolved.appearance.fontPreset).toBe("serif");
    expect(resolved.navigation.width).toBe(340);
    expect(resolved.navigation.collapsed).toBe(true);
    expect(resolved.branding.appName).toBe("Personal");
    expect(resolved.branding.subtitle).toBe("Workspace");
    expect(resolved.layout.startPage).toBe("/tasks");
    expect(resolved.dashboard.widgetSizes).toEqual({
      metrics: "wide",
      "recent-tasks": "wide",
      "recent-runs": "wide",
    });
  });

  it("round-trips the current JSON representation and imports legacy v1 themes", () => {
    const json = serializeFrontendCustomization(DEFAULT_FRONTEND_CUSTOMIZATION);
    expect(parseFrontendCustomizationJson(json)).toEqual(DEFAULT_FRONTEND_CUSTOMIZATION);
    expect(parseFrontendCustomizationJson('{"version":1,"appearance":{"accent":"#123456"}}')).toMatchObject({
      version: FRONTEND_CUSTOMIZATION_VERSION,
      appearance: { accent: "#123456" },
      layout: { startPage: "/" },
    });
    expect(() => parseFrontendCustomizationJson('{"version":99}')).toThrow(/Unsupported/);
    expect(() => parseFrontendCustomizationJson("{")).toThrow(/valid JSON/);
  });

  it("maps semantic customization values and dashboard sizing to centralized CSS variables", () => {
    const serifTheme = normalizeFrontendCustomization({
      ...DEFAULT_FRONTEND_CUSTOMIZATION,
      appearance: { ...DEFAULT_FRONTEND_CUSTOMIZATION.appearance, fontPreset: "serif" },
      dashboard: {
        ...DEFAULT_FRONTEND_CUSTOMIZATION.dashboard,
        widgetSizes: {
          ...DEFAULT_FRONTEND_CUSTOMIZATION.dashboard.widgetSizes,
          "recent-tasks": "wide",
        },
      },
    });
    const css = customizationCssVariables(serifTheme);
    expect(css["--page-background"]).toBe("#080b12");
    expect(css["--sidebar-width"]).toBe("276px");
    expect(css["--content-max-width"]).toBe("1500px");
    expect(css["--card-radius"]).toBe("14px");
    expect(css["--font-family"]).toContain("Georgia");
    expect(css["--dashboard-metrics-grid-column"]).toBe("1 / -1");
    expect(css["--dashboard-recent-tasks-grid-column"]).toBe("1 / -1");
    expect(css["--dashboard-recent-runs-grid-column"]).toBe("auto");
  });
});
