import { describe, expect, it, vi } from "vitest";
import { NotificationClient } from "./notifications";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("NotificationClient", () => {
  it("reads the canonical notification collection with filters", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "https://ui.example");
      expect(url.pathname).toBe("/api/v1/notifications");
      expect(url.searchParams.get("filter[state]")).toBe("unread");
      expect(url.searchParams.get("filter[severity]")).toBe("error");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new NotificationClient({ fetchImpl });

    await client.list({ filters: { state: "unread", severity: "error" } });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("uses exact lifecycle commands with idempotency keys", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown>; headers: Headers }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        headers: new Headers(init?.headers),
      });
      return jsonResponse({ id: "notification_1", type: "notification", state: "read" });
    });
    const client = new NotificationClient({ fetchImpl });

    await client.markRead("notification_1");
    await client.acknowledge("notification_1");
    await client.dismiss("notification_1");
    await client.archive("notification_1");
    await client.markAllRead();
    await client.retryDelivery("notification_1", "email");

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/commands/notification.mark-read",
      "/api/v1/commands/notification.acknowledge",
      "/api/v1/commands/notification.dismiss",
      "/api/v1/commands/notification.archive",
      "/api/v1/commands/notification.mark-all-read",
      "/api/v1/commands/notification.delivery.retry",
    ]);
    expect(calls[0]?.body.resource_ref).toBe("notification_1");
    expect(calls[4]?.body.resource_ref).toBe("notifications");
    expect(calls[5]?.body).toEqual({ resource_ref: "notification_1", channel_id: "email" });
    expect(calls.every((call) => call.headers.has("idempotency-key"))).toBe(true);
  });

  it("updates preferences only through the canonical preference command", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/notification.preference.update");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body).toEqual({
        resource_ref: "user_1",
        minimum_severity: "warning",
        aggregate_duplicates: false,
      });
      return jsonResponse({
        id: "user_1",
        type: "notification-preference",
        recipient: { type: "user", id: "user_1" },
        enabled_categories: ["task"],
        minimum_severity: "warning",
        project_ids: [],
        muted: false,
        in_app_enabled: true,
        external_channels: [],
        aggregate_duplicates: false,
        unread_count: 0,
      });
    });
    const client = new NotificationClient({ fetchImpl });

    await client.updatePreference("user_1", {
      minimum_severity: "warning",
      aggregate_duplicates: false,
    });
  });
});
