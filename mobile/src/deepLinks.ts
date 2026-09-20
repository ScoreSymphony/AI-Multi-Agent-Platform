import { normalizeServerUrl } from "./session";

export type MobileRouteKind =
  | "task"
  | "run"
  | "approval"
  | "verification"
  | "notification"
  | "artifact"
  | "result"
  | "agent"
  | "worker";

export interface MobilePairingLink {
  serverOrigin: string;
  pairingId: string;
  pairingCode: string;
  protocolVersion: 1;
}

export interface MobileRoute {
  kind: MobileRouteKind;
  id: string;
}

const ROUTE_KINDS = new Set<MobileRouteKind>([
  "task",
  "run",
  "approval",
  "verification",
  "notification",
  "artifact",
  "result",
  "agent",
  "worker",
]);
const CANONICAL_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

export function parseMobileDeepLink(value: string): MobileRoute | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "aiagentplatform:") return null;
  if (url.username || url.password || url.search || url.hash) return null;

  const kind = url.hostname as MobileRouteKind;
  let id: string;
  try {
    const encodedId = url.pathname.startsWith("/") ? url.pathname.slice(1) : url.pathname;
    id = decodeURIComponent(encodedId);
  } catch {
    return null;
  }
  if (!ROUTE_KINDS.has(kind) || !CANONICAL_ID.test(id)) return null;
  if (id.includes("/") || id.includes("\\")) return null;
  return { kind, id };
}

export function parseMobilePairingLink(value: string): MobilePairingLink | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "aiagentplatform:" || url.hostname !== "pair") return null;
  if (url.username || url.password || url.hash) return null;
  if (url.pathname !== "" && url.pathname !== "/") return null;

  const allowed = new Set(["v", "origin", "pairing_id", "code"]);
  for (const key of url.searchParams.keys()) {
    if (!allowed.has(key) || url.searchParams.getAll(key).length !== 1) return null;
  }
  if (url.searchParams.get("v") !== "1") return null;
  const pairingId = url.searchParams.get("pairing_id");
  const pairingCode = url.searchParams.get("code");
  const origin = url.searchParams.get("origin");
  if (!pairingId || !pairingCode || !origin) return null;
  if (!/^mobile_pairing_[0-9a-f-]{36}$/i.test(pairingId)) return null;
  if (!/^[A-HJ-NP-Z2-9]{8}-[A-HJ-NP-Z2-9]{20}$/.test(pairingCode)) return null;

  let serverOrigin: string;
  try {
    serverOrigin = normalizeServerUrl(origin);
  } catch {
    return null;
  }
  return {
    serverOrigin,
    pairingId,
    pairingCode,
    protocolVersion: 1,
  };
}

export function mobileRouteHref(route: MobileRoute): string {
  if (!ROUTE_KINDS.has(route.kind) || !CANONICAL_ID.test(route.id)) {
    throw new Error("Invalid canonical mobile route");
  }
  return `aiagentplatform://${route.kind}/${encodeURIComponent(route.id)}`;
}
