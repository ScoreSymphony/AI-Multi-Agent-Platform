import { afterEach, describe, expect, it, vi } from "vitest";
import { describeLiveStreamError, TaskEventStream } from "./live";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly close = vi.fn();
  private readonly listeners = new Map<string, EventListenerOrEventListenerObject[]>();

  constructor(url: string | URL, init?: EventSourceInit) {
    this.url = String(url);
    this.withCredentials = init?.withCredentials ?? false;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  emit(type: string, data: string): void {
    const event = { data } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) {
      if (typeof listener === "function") listener(event);
      else listener.handleEvent(event);
    }
  }
}

afterEach(() => {
  MockEventSource.instances = [];
  vi.unstubAllGlobals();
});

function installBrowserStubs(): void {
  vi.stubGlobal("window", { location: { origin: "https://ui.example" } });
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
}

describe("TaskEventStream", () => {
  it("uses the canonical Task SSE route with credentials and exposes reconnect state", () => {
    installBrowserStubs();
    const states: string[] = [];
    const stream = new TaskEventStream({
      baseUrl: "https://control.example/",
      taskId: "task id/with spaces",
      afterEventId: "event/42",
      onEvent: vi.fn(),
      onState: (state) => states.push(state),
    });

    stream.open();

    const source = MockEventSource.instances[0];
    const url = new URL(source.url);
    expect(url.origin).toBe("https://control.example");
    expect(url.pathname).toBe("/api/v1/tasks/task%20id%2Fwith%20spaces/events/stream");
    expect(url.searchParams.get("after_event_id")).toBe("event/42");
    expect(source.withCredentials).toBe(true);
    expect(states).toEqual(["connecting"]);

    source.onopen?.({} as Event);
    source.onerror?.({} as Event);
    stream.close();

    expect(states).toEqual(["connecting", "open", "reconnecting", "closed"]);
    expect(source.close).toHaveBeenCalledOnce();
  });

  it("delivers canonical events and canonical stream errors without inventing recovery semantics", () => {
    installBrowserStubs();
    const onEvent = vi.fn();
    const onError = vi.fn();
    const stream = new TaskEventStream({
      taskId: "task_123",
      onEvent,
      onError,
    });

    stream.open();
    const source = MockEventSource.instances[0];

    source.emit(
      "platform.event",
      JSON.stringify({
        id: "event_123",
        type: "event",
        event_type: "task.updated",
        occurred_at: "2026-09-03T15:00:00Z",
      }),
    );
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ id: "event_123" }));

    source.emit(
      "platform.error",
      JSON.stringify({
        code: "unavailable",
        category: "transport",
        message: "live stream temporarily unavailable",
        request_id: "request_123",
        correlation_id: "correlation_123",
        retryable: true,
      }),
    );
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "unavailable", request_id: "request_123" }),
    );

    source.emit("platform.event", "{");
    source.emit("platform.error", "{");
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "Malformed canonical event payload" }));
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "Malformed canonical stream error" }));
  });

  it("formats canonical stream errors with their request reference", () => {
    expect(
      describeLiveStreamError({
        code: "unavailable",
        category: "transport",
        message: "stream unavailable",
        request_id: "request_abc",
        correlation_id: "correlation_abc",
        retryable: true,
      }),
    ).toBe("transport/unavailable: stream unavailable · request request_abc");

    expect(describeLiveStreamError(new Error("Malformed canonical event payload"))).toBe(
      "Malformed canonical event payload",
    );
  });
});
