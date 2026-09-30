import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  DEFAULT_FRONTEND_CUSTOMIZATION,
  customizationCssVariables,
  normalizeFrontendCustomization,
  parseFrontendCustomizationJson,
  serializeFrontendCustomization,
  type FrontendCustomization,
} from "./model";

const STORAGE_KEY = "ai-multi-agent-platform.frontend-customization.v1";

interface FrontendCustomizationContextValue {
  customization: FrontendCustomization;
  draft: FrontendCustomization;
  hasPendingChanges: boolean;
  setDraft: Dispatch<SetStateAction<FrontendCustomization>>;
  save: () => void;
  discardPreview: () => void;
  resetToDefaults: () => void;
  importJson: (raw: string) => void;
  exportJson: () => string;
  hydrateSaved: (value: FrontendCustomization) => void;
}

function cloneDefault(): FrontendCustomization {
  return structuredClone(DEFAULT_FRONTEND_CUSTOMIZATION);
}

function loadStoredCustomization(): FrontendCustomization {
  if (typeof window === "undefined" || !window.localStorage) return cloneDefault();
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return cloneDefault();
  try {
    return parseFrontendCustomizationJson(raw);
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return cloneDefault();
  }
}

function persistCustomization(value: FrontendCustomization) {
  if (typeof window === "undefined" || !window.localStorage) return;
  window.localStorage.setItem(STORAGE_KEY, serializeFrontendCustomization(value));
}

function clearStoredCustomization() {
  if (typeof window === "undefined" || !window.localStorage) return;
  window.localStorage.removeItem(STORAGE_KEY);
}

function applyCustomization(value: FrontendCustomization) {
  if (typeof document === "undefined") return;
  const normalized = normalizeFrontendCustomization(value);
  const root = document.documentElement;
  for (const [name, cssValue] of Object.entries(customizationCssVariables(normalized))) {
    root.style.setProperty(name, cssValue);
  }
  root.dataset.frontendDensity = normalized.appearance.density;
  document.title = normalized.branding.appName;

  const existingIcon = document.head.querySelector<HTMLLinkElement>('link[data-customization-icon="true"]');
  if (normalized.branding.logoDataUrl) {
    const icon = existingIcon ?? document.createElement("link");
    icon.rel = "icon";
    icon.type = normalized.branding.logoDataUrl.startsWith("data:image/png")
      ? "image/png"
      : normalized.branding.logoDataUrl.startsWith("data:image/jpeg")
        ? "image/jpeg"
        : "image/webp";
    icon.href = normalized.branding.logoDataUrl;
    icon.dataset.customizationIcon = "true";
    if (!existingIcon) document.head.appendChild(icon);
  } else {
    existingIcon?.remove();
  }
}

const defaultValue: FrontendCustomizationContextValue = {
  customization: DEFAULT_FRONTEND_CUSTOMIZATION,
  draft: DEFAULT_FRONTEND_CUSTOMIZATION,
  hasPendingChanges: false,
  setDraft: () => undefined,
  save: () => undefined,
  discardPreview: () => undefined,
  resetToDefaults: () => undefined,
  importJson: () => undefined,
  exportJson: () => serializeFrontendCustomization(DEFAULT_FRONTEND_CUSTOMIZATION),
  hydrateSaved: () => undefined,
};

const FrontendCustomizationContext = createContext<FrontendCustomizationContextValue>(defaultValue);

export function FrontendCustomizationProvider({ children }: { children: ReactNode }) {
  const [customization, setCustomization] = useState<FrontendCustomization>(() => loadStoredCustomization());
  const [draft, setDraft] = useState<FrontendCustomization>(() => loadStoredCustomization());

  useEffect(() => {
    applyCustomization(draft);
  }, [draft]);

  const value = useMemo<FrontendCustomizationContextValue>(() => ({
    customization,
    draft,
    hasPendingChanges: serializeFrontendCustomization(customization) !== serializeFrontendCustomization(draft),
    setDraft,
    save: () => {
      const normalized = normalizeFrontendCustomization(draft);
      setCustomization(normalized);
      setDraft(normalized);
      persistCustomization(normalized);
    },
    discardPreview: () => {
      setDraft(customization);
    },
    resetToDefaults: () => {
      const defaults = cloneDefault();
      setCustomization(defaults);
      setDraft(defaults);
      clearStoredCustomization();
    },
    importJson: (raw: string) => {
      setDraft(parseFrontendCustomizationJson(raw));
    },
    exportJson: () => serializeFrontendCustomization(customization),
    hydrateSaved: (value: FrontendCustomization) => {
      const normalized = normalizeFrontendCustomization(value);
      setCustomization(normalized);
      setDraft(normalized);
      persistCustomization(normalized);
    },
  }), [customization, draft]);

  return (
    <FrontendCustomizationContext.Provider value={value}>
      {children}
    </FrontendCustomizationContext.Provider>
  );
}

export function useFrontendCustomization(): FrontendCustomizationContextValue {
  return useContext(FrontendCustomizationContext);
}
