import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

interface RouterValue {
  path: string;
  search: string;
  navigate: (path: string) => void;
}

interface RouterLocation {
  path: string;
  search: string;
}

const RouterContext = createContext<RouterValue | null>(null);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<RouterLocation>(readBrowserLocation);

  useEffect(() => {
    const onPopState = () => setLocation(readBrowserLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((next: string) => {
    if (typeof window === "undefined") return;
    const nextHref = normalizeInternalNavigationHref(next, window.location.href);
    if (!nextHref) return;

    const target = new URL(nextHref, window.location.origin);
    const normalizedPath = normalize(target.pathname);
    const currentHref = `${normalize(window.location.pathname)}${window.location.search}${window.location.hash}`;
    if (nextHref !== currentHref) {
      window.history.pushState({}, "", nextHref);
    }
    setLocation({ path: normalizedPath, search: target.search });
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  const value = useMemo(
    () => ({ path: location.path, search: location.search, navigate }),
    [location.path, location.search, navigate],
  );
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter must be used inside RouterProvider");
  return value;
}

export function AppLink({
  href,
  target,
  onClick,
  children,
  dangerouslySetInnerHTML: _dangerouslySetInnerHTML,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { navigate } = useRouter();
  const safeHref = normalizeAppLinkHref(href);
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || !safeHref
      || (target !== undefined && target !== "_self")
    ) {
      return;
    }

    const resolved = new URL(
      safeHref,
      typeof window === "undefined" ? "https://router.invalid/" : window.location.href,
    );
    if (
      typeof window !== "undefined"
      && resolved.origin !== window.location.origin
    ) {
      return;
    }

    event.preventDefault();
    navigate(`${resolved.pathname}${resolved.search}${resolved.hash}`);
  };
  return <a href={safeHref} target={target} onClick={handleClick} {...rest}>{children}</a>;
}

export function normalizeAppLinkHref(href: string | undefined): string | undefined {
  if (!href) return href;
  try {
    const base = new URL("https://router.invalid/");
    const resolved = new URL(href, base);
    if (resolved.protocol !== "http:" && resolved.protocol !== "https:") {
      return undefined;
    }
    if (resolved.origin === base.origin) {
      return `${resolved.pathname}${resolved.search}${resolved.hash}`;
    }
    return resolved.href;
  } catch {
    return undefined;
  }
}

export function normalizeInternalNavigationHref(
  href: string,
  currentHref: string,
): string | undefined {
  try {
    const current = new URL(currentHref);
    const target = new URL(href, current);
    if (
      (target.protocol !== "http:" && target.protocol !== "https:")
      || target.origin !== current.origin
    ) {
      return undefined;
    }
    const normalizedPath = normalize(target.pathname);
    return `${normalizedPath}${target.search}${target.hash}`;
  } catch {
    return undefined;
  }
}

export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const patternParts = normalize(pattern).split("/").filter(Boolean);
  const pathParts = normalize(path).split("/").filter(Boolean);
  if (patternParts.length !== pathParts.length) return null;
  const params: Record<string, string> = {};
  for (let index = 0; index < patternParts.length; index += 1) {
    const expected = patternParts[index];
    const actual = pathParts[index];
    if (expected.startsWith(":")) {
      try {
        params[expected.slice(1)] = decodeURIComponent(actual);
      } catch {
        return null;
      }
    } else if (expected !== actual) {
      return null;
    }
  }
  return params;
}

function readBrowserLocation(): RouterLocation {
  if (typeof window === "undefined") return { path: "/", search: "" };
  return {
    path: normalize(window.location.pathname),
    search: window.location.search ?? "",
  };
}

function normalize(path: string): string {
  if (!path || path === "/") return "/";
  return `/${path.split("/").filter(Boolean).join("/")}`;
}
