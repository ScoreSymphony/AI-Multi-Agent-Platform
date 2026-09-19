import { describe, expect, it } from "vitest";
import { matchPath } from "./router";

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
