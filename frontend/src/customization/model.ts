export const FRONTEND_CUSTOMIZATION_VERSION = 1;

export type FrontendDensity = "compact" | "comfortable" | "spacious";
export type NavigationPosition = "left" | "right";
export type ShadowPreset = "none" | "soft" | "medium";
export type FontPreset = "system" | "serif" | "monospace";
export type DashboardWidgetId = "metrics" | "recent-tasks" | "recent-runs";

export interface FrontendAppearance {
  background: string;
  backgroundHighlight: string;
  surface: string;
  surfaceSecondary: string;
  surfaceRaised: string;
  sidebar: string;
  line: string;
  lineSoft: string;
  muted: string;
  text: string;
  accent: string;
  accentStrong: string;
  danger: string;
  warning: string;
  success: string;
  fontScale: number;
  fontPreset: FontPreset;
  density: FrontendDensity;
  controlRadius: number;
  cardRadius: number;
  shadow: ShadowPreset;
}

export interface FrontendNavigationCustomization {
  position: NavigationPosition;
  width: number;
  collapsed: boolean;
  hiddenPaths: string[];
  order: string[];
}

export interface FrontendBranding {
  appName: string;
  subtitle: string;
  logoDataUrl: string | null;
}

export interface FrontendLayoutCustomization {
  maxContentWidth: number;
}

export interface FrontendDashboardCustomization {
  hiddenWidgets: DashboardWidgetId[];
  order: DashboardWidgetId[];
}

export interface FrontendCustomization {
  version: typeof FRONTEND_CUSTOMIZATION_VERSION;
  appearance: FrontendAppearance;
  navigation: FrontendNavigationCustomization;
  branding: FrontendBranding;
  layout: FrontendLayoutCustomization;
  dashboard: FrontendDashboardCustomization;
}

export interface FrontendCustomizationLayer {
  version?: typeof FRONTEND_CUSTOMIZATION_VERSION;
  appearance?: Partial<FrontendAppearance>;
  navigation?: Partial<FrontendNavigationCustomization>;
  branding?: Partial<FrontendBranding>;
  layout?: Partial<FrontendLayoutCustomization>;
  dashboard?: Partial<FrontendDashboardCustomization>;
}

export const DASHBOARD_WIDGETS: readonly DashboardWidgetId[] = [
  "metrics",
  "recent-tasks",
  "recent-runs",
];

export const MAX_BRANDING_IMAGE_BYTES = 256 * 1024;

export const DEFAULT_FRONTEND_CUSTOMIZATION: FrontendCustomization = {
  version: FRONTEND_CUSTOMIZATION_VERSION,
  appearance: {
    background: "#080b12",
    backgroundHighlight: "#13243d",
    surface: "#111722",
    surfaceSecondary: "#171f2d",
    surfaceRaised: "#1a2332",
    sidebar: "#0a0f18",
    line: "#293244",
    lineSoft: "#202838",
    muted: "#95a1b6",
    text: "#eef2ff",
    accent: "#7dd3fc",
    accentStrong: "#38bdf8",
    danger: "#fb7185",
    warning: "#fbbf24",
    success: "#4ade80",
    fontScale: 1,
    fontPreset: "system",
    density: "comfortable",
    controlRadius: 8,
    cardRadius: 14,
    shadow: "soft",
  },
  navigation: {
    position: "left",
    width: 276,
    collapsed: false,
    hiddenPaths: [],
    order: [],
  },
  branding: {
    appName: "Agent Platform",
    subtitle: "Control Plane",
    logoDataUrl: null,
  },
  layout: {
    maxContentWidth: 1500,
  },
  dashboard: {
    hiddenWidgets: [],
    order: [...DASHBOARD_WIDGETS],
  },
};

const COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const REQUIRED_NAVIGATION_PATHS = new Set(["/", "/settings"]);
const DENSITIES = new Set<FrontendDensity>(["compact", "comfortable", "spacious"]);
const FONT_PRESETS = new Set<FontPreset>(["system", "serif", "monospace"]);
const NAVIGATION_POSITIONS = new Set<NavigationPosition>(["left", "right"]);
const SHADOW_PRESETS = new Set<ShadowPreset>(["none", "soft", "medium"]);
const DASHBOARD_WIDGET_SET = new Set<DashboardWidgetId>(DASHBOARD_WIDGETS);
const BRANDING_IMAGE_PATTERN = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;
const MAX_BRANDING_DATA_URL_LENGTH = Math.ceil(MAX_BRANDING_IMAGE_BYTES * 1.4) + 128;

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function color(value: unknown, fallback: string): string {
  return typeof value === "string" && COLOR_PATTERN.test(value) ? value : fallback;
}

function numberInRange(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function shortText(value: unknown, fallback: string, maximum = 80): string {
  if (typeof value !== "string") return fallback;
  const trimmed = value.trim();
  return trimmed && trimmed.length <= maximum ? trimmed : fallback;
}

function logoDataUrl(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" || value.length > MAX_BRANDING_DATA_URL_LENGTH) return null;
  return BRANDING_IMAGE_PATTERN.test(value) ? value : null;
}

function pathList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value.filter(
        (entry): entry is string =>
          typeof entry === "string" &&
          entry.startsWith("/") &&
          entry.length <= 160 &&
          !entry.includes("\n") &&
          !entry.includes("\r"),
      ),
    ),
  ).slice(0, 250);
}

function dashboardWidgetList(value: unknown): DashboardWidgetId[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value.filter(
        (entry): entry is DashboardWidgetId =>
          typeof entry === "string" && DASHBOARD_WIDGET_SET.has(entry as DashboardWidgetId),
      ),
    ),
  );
}

export function normalizeFrontendCustomization(value: unknown): FrontendCustomization {
  const source = record(value);
  if (source?.version !== FRONTEND_CUSTOMIZATION_VERSION) {
    return structuredClone(DEFAULT_FRONTEND_CUSTOMIZATION);
  }

  const appearance = record(source.appearance) ?? {};
  const navigation = record(source.navigation) ?? {};
  const branding = record(source.branding) ?? {};
  const layout = record(source.layout) ?? {};
  const dashboard = record(source.dashboard) ?? {};
  const defaults = DEFAULT_FRONTEND_CUSTOMIZATION;

  const density = DENSITIES.has(appearance.density as FrontendDensity)
    ? appearance.density as FrontendDensity
    : defaults.appearance.density;
  const fontPreset = FONT_PRESETS.has(appearance.fontPreset as FontPreset)
    ? appearance.fontPreset as FontPreset
    : defaults.appearance.fontPreset;
  const position = NAVIGATION_POSITIONS.has(navigation.position as NavigationPosition)
    ? navigation.position as NavigationPosition
    : defaults.navigation.position;
  const shadow = SHADOW_PRESETS.has(appearance.shadow as ShadowPreset)
    ? appearance.shadow as ShadowPreset
    : defaults.appearance.shadow;

  const requestedDashboardOrder = dashboardWidgetList(dashboard.order);
  const dashboardOrder = [
    ...requestedDashboardOrder,
    ...DASHBOARD_WIDGETS.filter((widget) => !requestedDashboardOrder.includes(widget)),
  ];

  return {
    version: FRONTEND_CUSTOMIZATION_VERSION,
    appearance: {
      background: color(appearance.background, defaults.appearance.background),
      backgroundHighlight: color(appearance.backgroundHighlight, defaults.appearance.backgroundHighlight),
      surface: color(appearance.surface, defaults.appearance.surface),
      surfaceSecondary: color(appearance.surfaceSecondary, defaults.appearance.surfaceSecondary),
      surfaceRaised: color(appearance.surfaceRaised, defaults.appearance.surfaceRaised),
      sidebar: color(appearance.sidebar, defaults.appearance.sidebar),
      line: color(appearance.line, defaults.appearance.line),
      lineSoft: color(appearance.lineSoft, defaults.appearance.lineSoft),
      muted: color(appearance.muted, defaults.appearance.muted),
      text: color(appearance.text, defaults.appearance.text),
      accent: color(appearance.accent, defaults.appearance.accent),
      accentStrong: color(appearance.accentStrong, defaults.appearance.accentStrong),
      danger: color(appearance.danger, defaults.appearance.danger),
      warning: color(appearance.warning, defaults.appearance.warning),
      success: color(appearance.success, defaults.appearance.success),
      fontScale: numberInRange(appearance.fontScale, defaults.appearance.fontScale, 0.85, 1.25),
      fontPreset,
      density,
      controlRadius: numberInRange(appearance.controlRadius, defaults.appearance.controlRadius, 0, 20),
      cardRadius: numberInRange(appearance.cardRadius, defaults.appearance.cardRadius, 0, 28),
      shadow,
    },
    navigation: {
      position,
      width: numberInRange(navigation.width, defaults.navigation.width, 220, 420),
      collapsed: booleanValue(navigation.collapsed, defaults.navigation.collapsed),
      hiddenPaths: pathList(navigation.hiddenPaths).filter((path) => !REQUIRED_NAVIGATION_PATHS.has(path)),
      order: pathList(navigation.order),
    },
    branding: {
      appName: shortText(branding.appName, defaults.branding.appName),
      subtitle: shortText(branding.subtitle, defaults.branding.subtitle),
      logoDataUrl: logoDataUrl(branding.logoDataUrl),
    },
    layout: {
      maxContentWidth: numberInRange(layout.maxContentWidth, defaults.layout.maxContentWidth, 960, 2200),
    },
    dashboard: {
      hiddenWidgets: dashboardWidgetList(dashboard.hiddenWidgets),
      order: dashboardOrder,
    },
  };
}

export function resolveFrontendCustomizationLayers(
  ...layers: readonly FrontendCustomizationLayer[]
): FrontendCustomization {
  const merged = structuredClone(DEFAULT_FRONTEND_CUSTOMIZATION);

  for (const layer of layers) {
    if (layer.version !== undefined && layer.version !== FRONTEND_CUSTOMIZATION_VERSION) continue;
    if (layer.appearance) Object.assign(merged.appearance, layer.appearance);
    if (layer.navigation) Object.assign(merged.navigation, layer.navigation);
    if (layer.branding) Object.assign(merged.branding, layer.branding);
    if (layer.layout) Object.assign(merged.layout, layer.layout);
    if (layer.dashboard) Object.assign(merged.dashboard, layer.dashboard);
  }

  return normalizeFrontendCustomization(merged);
}

export function parseFrontendCustomizationJson(raw: string): FrontendCustomization {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("Customization import must be valid JSON.");
  }
  const source = record(parsed);
  if (source?.version !== FRONTEND_CUSTOMIZATION_VERSION) {
    throw new Error(`Unsupported customization schema version. Expected ${FRONTEND_CUSTOMIZATION_VERSION}.`);
  }
  return normalizeFrontendCustomization(parsed);
}

export function serializeFrontendCustomization(value: FrontendCustomization): string {
  return JSON.stringify(normalizeFrontendCustomization(value), null, 2);
}

export function customizationCssVariables(value: FrontendCustomization): Record<string, string> {
  const normalized = normalizeFrontendCustomization(value);
  const { appearance } = normalized;
  const densityScale = appearance.density === "compact" ? 0.86 : appearance.density === "spacious" ? 1.14 : 1;
  const shadow = appearance.shadow === "none"
    ? "none"
    : appearance.shadow === "medium"
      ? "0 16px 42px rgba(0, 0, 0, .22)"
      : "0 12px 32px rgba(0, 0, 0, .12)";
  const fontFamily = appearance.fontPreset === "serif"
    ? 'Georgia, "Times New Roman", serif'
    : appearance.fontPreset === "monospace"
      ? '"SFMono-Regular", Consolas, "Liberation Mono", monospace'
      : 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

  return {
    "--page-background": appearance.background,
    "--page-highlight": appearance.backgroundHighlight,
    "--surface": appearance.surface,
    "--surface-2": appearance.surfaceSecondary,
    "--surface-raised": appearance.surfaceRaised,
    "--sidebar": appearance.sidebar,
    "--line": appearance.line,
    "--line-soft": appearance.lineSoft,
    "--muted": appearance.muted,
    "--text": appearance.text,
    "--accent": appearance.accent,
    "--accent-strong": appearance.accentStrong,
    "--danger": appearance.danger,
    "--warning": appearance.warning,
    "--success": appearance.success,
    "--font-family": fontFamily,
    "--font-scale": String(appearance.fontScale),
    "--density-scale": String(densityScale),
    "--control-radius": `${appearance.controlRadius}px`,
    "--card-radius": `${appearance.cardRadius}px`,
    "--card-shadow": shadow,
    "--sidebar-width": `${normalized.navigation.width}px`,
    "--content-max-width": `${normalized.layout.maxContentWidth}px`,
  };
}

export function resolveCustomizedNavigation<T extends { path: string }>(
  items: readonly T[],
  customization: FrontendNavigationCustomization,
): T[] {
  const hidden = new Set(
    customization.hiddenPaths.filter((path) => !REQUIRED_NAVIGATION_PATHS.has(path)),
  );
  const order = new Map(customization.order.map((path, index) => [path, index]));
  const canonicalOrder = new Map(items.map((item, index) => [item.path, index]));

  return items
    .filter((item) => !hidden.has(item.path))
    .slice()
    .sort((left, right) => {
      const leftOrder = order.get(left.path);
      const rightOrder = order.get(right.path);
      if (leftOrder !== undefined || rightOrder !== undefined) {
        if (leftOrder === undefined) return 1;
        if (rightOrder === undefined) return -1;
        if (leftOrder !== rightOrder) return leftOrder - rightOrder;
      }
      return (canonicalOrder.get(left.path) ?? 0) - (canonicalOrder.get(right.path) ?? 0);
    });
}

export function resolveDashboardWidgets(
  customization: FrontendDashboardCustomization,
): DashboardWidgetId[] {
  const hidden = new Set(customization.hiddenWidgets);
  const requestedOrder = dashboardWidgetList(customization.order);
  const completeOrder = [
    ...requestedOrder,
    ...DASHBOARD_WIDGETS.filter((widget) => !requestedOrder.includes(widget)),
  ];
  return completeOrder.filter((widget) => !hidden.has(widget));
}

export function navigationPathCanBeHidden(path: string): boolean {
  return !REQUIRED_NAVIGATION_PATHS.has(path);
}
