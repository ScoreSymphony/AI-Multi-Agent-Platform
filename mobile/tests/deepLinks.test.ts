import { describe, expect, it } from "vitest";

import {
  mobileRouteHref,
  parseMobileDeepLink,
  parseMobilePairingLink,
} from "../src/deepLinks";

describe("mobile deep links", () => {
  it("accepts only canonical allowlisted resource routes", () => {
    expect(parseMobileDeepLink("aiagentplatform://task/task-123")).toEqual({
      kind: "task",
      id: "task-123",
    });
    expect(mobileRouteHref({ kind: "approval", id: "approval:42" })).toBe(
      "aiagentplatform://approval/approval%3A42",
    );
  });

  it("accepts only the versioned HTTPS mobile pairing deep link", () => {
    const value =
      "aiagentplatform://pair?v=1&origin=https%3A%2F%2Fplatform.example" +
      "&pairing_id=mobile_pairing_123e4567-e89b-12d3-a456-426614174000" +
      "&code=ABCDEFGH-JKLMNPQRSTUVWX234567";
    expect(parseMobilePairingLink(value)).toEqual({
      serverOrigin: "https://platform.example",
      pairingId: "mobile_pairing_123e4567-e89b-12d3-a456-426614174000",
      pairingCode: "ABCDEFGH-JKLMNPQRSTUVWX234567",
      protocolVersion: 1,
    });
    expect(
      parseMobilePairingLink(
        value.replace("https%3A%2F%2Fplatform.example", "http%3A%2F%2Fplatform.example"),
      ),
    ).toBeNull();
    expect(parseMobilePairingLink(value.replace("v=1", "v=2"))).toBeNull();
    expect(parseMobilePairingLink(`${value}&action=approve`)).toBeNull();
  });

  it("rejects arbitrary schemes, query actions and path injection", () => {
    expect(parseMobileDeepLink("https://evil.example/task/1")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://task/a/b")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://task/task-1?action=approve")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://unknown/id")).toBeNull();
    expect(parseMobileDeepLink("aiagentplatform://task/%E0%A4%A")).toBeNull();
  });
});
