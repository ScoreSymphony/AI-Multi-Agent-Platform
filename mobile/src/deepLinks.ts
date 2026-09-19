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

export function mobileRouteHref(route: MobileRoute): string {
  if (!ROUTE_KINDS.has(route.kind) || !CANONICAL_ID.test(route.id)) {
    throw new Error("Invalid canonical mobile route");
  }
  return `aiagentplatform://${route.kind}/${encodeURIComponent(route.id)}`;
}
