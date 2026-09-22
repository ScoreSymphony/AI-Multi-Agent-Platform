export interface BrowserAuthenticationLocation {
  protocol: string;
  hostname: string;
  origin?: string;
}

export interface BrowserAuthenticationTransport {
  supported: boolean;
  mode: "secure" | "loopback-http" | "non-browser" | "unsupported";
  origin: string | null;
}

export function classifyBrowserAuthenticationTransport(
  location: BrowserAuthenticationLocation | null,
): BrowserAuthenticationTransport {
  if (location === null) {
    return { supported: true, mode: "non-browser", origin: null };
  }

  const protocol = location.protocol.toLowerCase();
  const hostname = normalizeHostname(location.hostname);

  if (protocol === "https:") {
    return { supported: true, mode: "secure", origin: location.origin ?? null };
  }

  if (protocol === "http:" && isLoopbackHostname(hostname)) {
    return { supported: true, mode: "loopback-http", origin: location.origin ?? null };
  }

  return { supported: false, mode: "unsupported", origin: location.origin ?? null };
}

export function currentBrowserAuthenticationTransport(): BrowserAuthenticationTransport {
  if (typeof window === "undefined") {
    return classifyBrowserAuthenticationTransport(null);
  }
  return classifyBrowserAuthenticationTransport(window.location);
}

function normalizeHostname(hostname: string): string {
  const normalized = hostname.trim().toLowerCase();
  if (normalized.startsWith("[") && normalized.endsWith("]")) {
    return normalized.slice(1, -1);
  }
  return normalized;
}

function isLoopbackHostname(hostname: string): boolean {
  return (
    hostname === "localhost"
    || hostname.endsWith(".localhost")
    || hostname === "::1"
    || hostname === "127.0.0.1"
    || hostname.startsWith("127.")
  );
}
