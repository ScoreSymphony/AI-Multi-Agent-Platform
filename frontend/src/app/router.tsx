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
    const target = new URL(next, window.location.href);
    if (target.origin !== window.location.origin) {
      window.location.assign(target.href);
      return;
    }

    const normalizedPath = normalize(target.pathname);
    const nextHref = `${normalizedPath}${target.search}${target.hash}`;
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
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { navigate } = useRouter();
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || !href
      || (target !== undefined && target !== "_self")
    ) {
      return;
    }

    const resolved = new URL(
      href,
      typeof window === "undefined" ? "http://router.invalid/" : window.location.href,
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
  return <a href={href} target={target} onClick={handleClick} {...rest} />;
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
