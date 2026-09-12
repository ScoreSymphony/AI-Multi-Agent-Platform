import type { CanonicalConversationMessage } from "./conversations";
import { ApiTransport, ControlPlaneError } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { APIErrorBody } from "./types";

export interface ConversationResponseDeltaEvent {
  id: string;
  type: "conversation.response.delta";
  conversation_id: string;
  source_message_id: string;
  authoritative: false;
  tentative: true;
  delta: { kind: "text"; text: string };
  model_config_id: string | null;
}

export interface ConversationResponseActivityEvent {
  id: string;
  type: "conversation.response.activity";
  conversation_id: string;
  source_message_id: string;
  authoritative: false;
  tentative: true;
  summary: string;
  model_config_id: string | null;
}

export interface ConversationResponseCommittedEvent {
  id: string;
  type: "conversation.response.committed";
  conversation_id: string;
  source_message_id: string;
  authoritative: false;
  tentative: false;
  durable: true;
  replayed: boolean;
  message: CanonicalConversationMessage;
}

export type ConversationResponseEvent =
  | ConversationResponseDeltaEvent
  | ConversationResponseActivityEvent
  | ConversationResponseCommittedEvent;

export interface ConversationResponseHandlers {
  onDelta?: (event: ConversationResponseDeltaEvent) => void;
  onActivity?: (event: ConversationResponseActivityEvent) => void;
  onCommitted?: (event: ConversationResponseCommittedEvent) => void;
}

export interface ConversationResponseClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class ConversationResponseClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: ConversationResponseClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  async stream(
    messageId: string,
    handlers: ConversationResponseHandlers = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<ConversationResponseCommittedEvent> {
    if (!idempotencyKey.trim()) throw new Error("conversation response idempotency key is required");
    const response = await this.transport.requestRaw(
      `/conversation-messages/${encodeURIComponent(messageId)}/response/stream`,
      {
        method: "POST",
        headers: { Accept: "text/event-stream" },
        idempotencyKey,
        retry: "never",
      },
    );
    if (!response.body) {
      throw new Error("Conversation response stream returned no body");
    }

    let committed: ConversationResponseCommittedEvent | null = null;
    for await (const frame of parseSSE(response.body)) {
      const payload = parseEventJson(frame.data);
      if (frame.event === "platform.error") {
        if (isErrorBody(payload)) {
          throw new ControlPlaneError(500, payload);
        }
        throw new Error("Malformed Conversation response stream error");
      }
      const event = parseResponseEvent(frame.event, payload);
      if (!event) continue;
      if (event.type === "conversation.response.delta") handlers.onDelta?.(event);
      else if (event.type === "conversation.response.activity") handlers.onActivity?.(event);
      else {
        committed = event;
        handlers.onCommitted?.(event);
      }
    }
    if (!committed) {
      throw new Error("Conversation response stream ended without a committed message");
    }
    return committed;
  }
}

export interface SSEFrame {
  event: string;
  data: string;
}

export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<SSEFrame> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseSSEBlock(block);
        if (frame) yield frame;
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    const tail = parseSSEBlock(buffer.trim());
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

function parseSSEBlock(block: string): SSEFrame | null {
  if (!block) return null;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}

function parseResponseEvent(eventName: string, payload: unknown): ConversationResponseEvent | null {
  if (typeof payload !== "object" || payload === null) return null;
  const candidate = payload as Partial<ConversationResponseEvent>;
  if (candidate.type !== eventName) return null;
  if (candidate.type === "conversation.response.delta") {
    return candidate as ConversationResponseDeltaEvent;
  }
  if (candidate.type === "conversation.response.activity") {
    return candidate as ConversationResponseActivityEvent;
  }
  if (candidate.type === "conversation.response.committed") {
    return candidate as ConversationResponseCommittedEvent;
  }
  return null;
}

function parseEventJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function isErrorBody(value: unknown): value is APIErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<APIErrorBody>;
  return (
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}
