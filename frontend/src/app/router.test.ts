import { describe, expect, it } from "vitest";
import {
  matchPath,
  normalizeAppLinkHref,
  normalizeInternalNavigationHref,
} from "./router";

describe("cross-cutting route matching", () => {
  it("decodes canonical route parameters without crashing on malformed deep links", () => {
    expect(matchPath("/tasks/:taskId", "/tasks/task_123")).toEqual({ taskId: "task_123" });
    expect(matchPath("/search/:term", "/search/hello%20world")).toEqual({ term: "hello world" });
    expect(matchPath("/tasks/:taskId", "/tasks/task%ZZ")).toBeNull();
  });

  it("rejects extra path segments instead of partially matching a maintained route", () => {
    expect(matchPath("/tasks/:taskId", "/tasks/task_123/extra")).toBeNull();
  });
});

describe("AppLink URL safety", () => {
  it("normalizes internal links and preserves safe external HTTP(S) links", () => {
    expect(normalizeAppLinkHref("/tasks/task_123?tab=runs#latest"))
      .toBe("/tasks/task_123?tab=runs#latest");
    expect(normalizeAppLinkHref("https://docs.example.test/guide?topic=router#safe"))
      .toBe("https://docs.example.test/guide?topic=router#safe");
    expect(normalizeAppLinkHref("http://docs.example.test:8080/guide"))
      .toBe("http://docs.example.test:8080/guide");
    expect(normalizeAppLinkHref("//docs.example.test/guide"))
      .toBe("https://docs.example.test/guide");
  });

  it("rejects executable and malformed URL schemes", () => {
    expect(normalizeAppLinkHref("javascript:alert(1)")).toBeUndefined();
    expect(normalizeAppLinkHref("data:text/html,<script>alert(1)</script>")).toBeUndefined();
    expect(normalizeAppLinkHref("vbscript:msgbox(1)")).toBeUndefined();
    expect(normalizeAppLinkHref("blob:https://router.invalid/id")).toBeUndefined();
    expect(normalizeAppLinkHref("http://[")).toBeUndefined();
  });
});

describe("router navigation URL safety", () => {
  const currentHref = "https://app.example.test/current?from=old#previous";

  it("normalizes only same-origin navigation targets", () => {
    expect(normalizeInternalNavigationHref("/tasks/task_123?tab=runs#latest", currentHref))
      .toBe("/tasks/task_123?tab=runs#latest");
    expect(
      normalizeInternalNavigationHref(
        "https://app.example.test/search?q=canonical#results",
        currentHref,
      ),
    ).toBe("/search?q=canonical#results");
  });

  it("rejects cross-origin, protocol-relative, executable and malformed targets", () => {
    expect(
      normalizeInternalNavigationHref("https://evil.example.test/phish", currentHref),
    ).toBeUndefined();
    expect(
      normalizeInternalNavigationHref("//evil.example.test/phish", currentHref),
    ).toBeUndefined();
    expect(normalizeInternalNavigationHref("javascript:alert(1)", currentHref)).toBeUndefined();
    expect(normalizeInternalNavigationHref("data:text/html,malicious", currentHref)).toBeUndefined();
    expect(normalizeInternalNavigationHref("http://[", currentHref)).toBeUndefined();
  });
});
