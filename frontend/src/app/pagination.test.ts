import { describe, expect, it } from "vitest";
import {
  advanceCursorPagination,
  initialCursorPagination,
  retreatCursorPagination,
} from "./pagination";

describe("opaque cursor pagination", () => {
  it("stores server cursors without decoding or rewriting them", () => {
    const initial = initialCursorPagination("tasks:priority:desc");
    const second = advanceCursorPagination(initial, "opaque/server+cursor==");
    expect(second.cursor).toBe("opaque/server+cursor==");
    expect(second.history).toEqual([null]);
    expect(second.pageNumber).toBe(2);
  });

  it("walks backward using only cursors previously received from the server", () => {
    const first = initialCursorPagination("tasks");
    const second = advanceCursorPagination(first, "cursor-2");
    const third = advanceCursorPagination(second, "cursor-3");
    expect(retreatCursorPagination(third)).toEqual(second);
    expect(retreatCursorPagination(second)).toEqual(first);
  });

  it("does not advance when the server reports no next cursor", () => {
    const state = initialCursorPagination("tasks");
    expect(advanceCursorPagination(state, null)).toBe(state);
    expect(retreatCursorPagination(state)).toBe(state);
  });
});
