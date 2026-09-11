import type {
  ConversationResponseActivityEvent,
  ConversationResponseDeltaEvent,
} from "../../api/conversationResponses";
import type { CanonicalConversationMessage } from "../../api/conversations";

export interface TentativeResponseState {
  sourceMessageId: string;
  text: string;
  activity: string | null;
  modelConfigId: string | null;
}

export function applyResponseDelta(
  current: TentativeResponseState | null,
  event: ConversationResponseDeltaEvent,
): TentativeResponseState {
  const base = current?.sourceMessageId === event.source_message_id
    ? current
    : {
        sourceMessageId: event.source_message_id,
        text: "",
        activity: null,
        modelConfigId: null,
      };
  return {
    ...base,
    text: `${base.text}${event.delta.text}`,
    modelConfigId: event.model_config_id ?? base.modelConfigId,
  };
}

export function applyResponseActivity(
  current: TentativeResponseState | null,
  event: ConversationResponseActivityEvent,
): TentativeResponseState {
  const base = current?.sourceMessageId === event.source_message_id
    ? current
    : {
        sourceMessageId: event.source_message_id,
        text: "",
        activity: null,
        modelConfigId: null,
      };
  return {
    ...base,
    activity: event.summary,
    modelConfigId: event.model_config_id ?? base.modelConfigId,
  };
}

export function upsertMessage(
  messages: CanonicalConversationMessage[],
  message: CanonicalConversationMessage,
): CanonicalConversationMessage[] {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index < 0) return [...messages, message];
  return messages.map((item, itemIndex) => (itemIndex === index ? message : item));
}
