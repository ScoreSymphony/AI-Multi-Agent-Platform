import { describe, expect, it } from "vitest";
import { matchPath, normalizeAppLinkHref } from "./router";

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
  const base = "https://app.example.test/current?view=1";

  it("normalizes same-origin links and preserves safe external HTTP(S) links", () => {
    expect(normalizeAppLinkHref("/tasks/task_123?tab=runs#latest", base))
      .toBe("/tasks/task_123?tab=runs#latest");
    expect(normalizeAppLinkHref("https://docs.example.test/guide", base))
      .toBe("https://docs.example.test/guide");
  });

  it("rejects executable and malformed URL schemes", () => {
    expect(normalizeAppLinkHref("javascript:alert(1)", base)).toBeUndefined();
    expect(normalizeAppLinkHref("data:text/html,<script>alert(1)</script>", base)).toBeUndefined();
    expect(normalizeAppLinkHref("vbscript:msgbox(1)", base)).toBeUndefined();
    expect(normalizeAppLinkHref("http://[", base)).toBeUndefined();
  });
});
