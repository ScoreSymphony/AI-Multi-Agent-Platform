import type { APIErrorBody, CanonicalEvent } from "./types";

export type LiveConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export interface TaskEventStreamOptions {
  baseUrl?: string;
  taskId: string;
  afterEventId?: string;
  onEvent: (event: CanonicalEvent) => void;
  onError?: (error: APIErrorBody | Error) => void;
  onState?: (state: LiveConnectionState) => void;
}

export class TaskEventStream {
  private source: EventSource | null = null;
  private readonly options: TaskEventStreamOptions;

  constructor(options: TaskEventStreamOptions) {
    this.options = options;
  }

  open(): void {
    this.close();
    this.options.onState?.("connecting");
    const base = (this.options.baseUrl ?? "").replace(/\/$/, "");
    const url = new URL(
      `${base}/api/v1/tasks/${encodeURIComponent(this.options.taskId)}/events/stream`,
      window.location.origin,
    );
    if (this.options.afterEventId) {
      url.searchParams.set("after_event_id", this.options.afterEventId);
    }
    const source = new EventSource(url, { withCredentials: true });
    this.source = source;
    source.onopen = () => this.options.onState?.("open");
    source.addEventListener("platform.event", (message) => {
      try {
        const event = JSON.parse((message as MessageEvent<string>).data) as CanonicalEvent;
        this.options.onEvent(event);
      } catch {
        this.options.onError?.(new Error("Malformed canonical event payload"));
      }
    });
    source.addEventListener("platform.error", (message) => {
      try {
        const error = JSON.parse((message as MessageEvent<string>).data) as APIErrorBody;
        this.options.onError?.(error);
      } catch {
        this.options.onError?.(new Error("Malformed canonical stream error"));
      }
    });
    source.onerror = () => this.options.onState?.("reconnecting");
  }

  close(): void {
    if (this.source) {
      this.source.close();
      this.source = null;
      this.options.onState?.("closed");
    }
  }
}
