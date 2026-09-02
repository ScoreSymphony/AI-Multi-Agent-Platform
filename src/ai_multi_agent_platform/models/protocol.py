"""Rich provider-neutral model request/response structures for issue #10.

The core provider interface from issue #5 remains source-compatible. These
structures encode into that stable ``ModelRequest`` envelope without importing
provider SDK types or provider-native model identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts import (
    JsonValue,
    ModelRequest,
    ModelResponse,
    OperationContext,
)


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelContentKind(StrEnum):
    TEXT = "text"
    JSON = "json"
    IMAGE_REF = "image_ref"
    FILE_REF = "file_ref"


class StructuredResponseKind(StrEnum):
    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class ModelFinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ModelContentBlock:
    """One backend-neutral content block."""

    kind: ModelContentKind
    text: str | None = None
    value: JsonValue = None
    ref: str | None = None

    def __post_init__(self) -> None:
        populated = sum(item is not None for item in (self.text, self.value, self.ref))
        if populated != 1:
            raise ValueError("content block must contain exactly one payload")
        if self.kind is ModelContentKind.TEXT:
            if self.text is None or not self.text:
                raise ValueError("text content blocks require non-empty text")
        elif self.kind is ModelContentKind.JSON:
            if self.value is None:
                raise ValueError("JSON content blocks require a value")
        elif self.ref is None or not self.ref.strip():
            raise ValueError("reference content blocks require a non-blank ref")

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {"kind": self.kind.value}
        if self.text is not None:
            result["text"] = self.text
        if self.value is not None:
            result["value"] = self.value
        if self.ref is not None:
            result["ref"] = self.ref
        return result


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: tuple[ModelContentBlock, ...]
    name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("model message must contain at least one content block")
        if self.name is not None and not self.name.strip():
            raise ValueError("model message name must not be blank")
        if self.tool_call_id is not None and not self.tool_call_id.strip():
            raise ValueError("tool_call_id must not be blank")
        if self.role is ModelRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool messages require tool_call_id")

    @classmethod
    def text(cls, role: ModelRole, text: str) -> ModelMessage:
        return cls(role=role, content=(ModelContentBlock(ModelContentKind.TEXT, text=text),))

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "role": self.role.value,
            "content": [block.to_json() for block in self.content],
        }
        if self.name is not None:
            result["name"] = self.name
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        return result


@dataclass(frozen=True, slots=True)
class ModelToolDefinition:
    tool_ref: str
    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_ref.strip():
            raise ValueError("tool_ref must not be blank")
        if not self.name.strip():
            raise ValueError("tool name must not be blank")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tool_ref": self.tool_ref,
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class StructuredResponseExpectation:
    kind: StructuredResponseKind = StructuredResponseKind.TEXT
    schema_name: str | None = None
    json_schema: dict[str, JsonValue] | None = None
    strict: bool = False

    def __post_init__(self) -> None:
        if self.kind is StructuredResponseKind.JSON_SCHEMA:
            if self.json_schema is None:
                raise ValueError("json_schema response requires a schema")
            if self.schema_name is None or not self.schema_name.strip():
                raise ValueError("json_schema response requires schema_name")
        elif self.json_schema is not None or self.schema_name is not None:
            raise ValueError("schema fields are only valid for json_schema responses")

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "kind": self.kind.value,
            "strict": self.strict,
        }
        if self.schema_name is not None:
            result["schema_name"] = self.schema_name
        if self.json_schema is not None:
            result["json_schema"] = dict(self.json_schema)
        return result


@dataclass(frozen=True, slots=True)
class ModelGenerationParameters:
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    stop: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.temperature is not None and self.temperature < 0:
            raise ValueError("temperature must not be negative")
        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p must be between 0 and 1")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")
        if any(not item for item in self.stop):
            raise ValueError("stop sequences must not be empty")

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.top_p is not None:
            result["top_p"] = self.top_p
        if self.max_tokens is not None:
            result["max_tokens"] = self.max_tokens
        if self.seed is not None:
            result["seed"] = self.seed
        if self.stop:
            result["stop"] = list(self.stop)
        return result


@dataclass(frozen=True, slots=True)
class CanonicalModelRequest:
    """Rich model request independent from any model API vendor."""

    request_id: str
    context: OperationContext
    messages: tuple[ModelMessage, ...]
    system_instruction: str | None = None
    tools: tuple[ModelToolDefinition, ...] = ()
    response: StructuredResponseExpectation = field(default_factory=StructuredResponseExpectation)
    generation: ModelGenerationParameters = field(default_factory=ModelGenerationParameters)
    model_config_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    routing_requirements: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must not be blank")
        if not self.messages:
            raise ValueError("canonical model request requires messages")
        for value, name in (
            (self.system_instruction, "system_instruction"),
            (self.model_config_id, "model_config_id"),
            (self.task_id, "task_id"),
            (self.run_id, "run_id"),
            (self.agent_id, "agent_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")

    def to_contract_request(self) -> ModelRequest:
        """Encode rich semantics into the stable issue-#5 request envelope."""

        canonical_messages = list(self.messages)
        if self.system_instruction is not None:
            canonical_messages.insert(
                0,
                ModelMessage.text(ModelRole.SYSTEM, self.system_instruction),
            )

        requirements = dict(self.routing_requirements)
        requirements["canonical_messages"] = [message.to_json() for message in canonical_messages]
        requirements["canonical_tools"] = [tool.to_json() for tool in self.tools]
        requirements["response_expectation"] = self.response.to_json()
        requirements.update(self.generation.to_json())

        if self.model_config_id is not None:
            requirements["model_config_id"] = self.model_config_id
        for key, value in (
            ("task_id", self.task_id),
            ("run_id", self.run_id),
            ("agent_id", self.agent_id),
        ):
            if value is not None:
                requirements[key] = value

        legacy_messages = tuple(_message_text(message) for message in canonical_messages)
        return ModelRequest(
            request_id=self.request_id,
            messages=legacy_messages,
            context=self.context,
            requirements=requirements,
        )


@dataclass(frozen=True, slots=True)
class ModelToolCallRequest:
    call_id: str
    tool_name: str
    arguments: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id.strip():
            raise ValueError("tool call ID must not be blank")
        if not self.tool_name.strip():
            raise ValueError("tool call name must not be blank")


@dataclass(frozen=True, slots=True)
class ModelTiming:
    latency_ms: float | None = None
    started_unix_ms: float | None = None
    ended_unix_ms: float | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.latency_ms, "latency_ms"),
            (self.started_unix_ms, "started_unix_ms"),
            (self.ended_unix_ms, "ended_unix_ms"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if (
            self.started_unix_ms is not None
            and self.ended_unix_ms is not None
            and self.ended_unix_ms < self.started_unix_ms
        ):
            raise ValueError("ended_unix_ms must not precede started_unix_ms")


@dataclass(frozen=True, slots=True)
class CanonicalModelResponse:
    request_id: str
    model_config_id: str
    content: tuple[ModelContentBlock, ...]
    tool_calls: tuple[ModelToolCallRequest, ...] = ()
    structured_output: JsonValue = None
    finish_reason: ModelFinishReason = ModelFinishReason.UNKNOWN
    usage: dict[str, JsonValue] = field(default_factory=dict)
    timing: ModelTiming = field(default_factory=ModelTiming)

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("response request_id must not be blank")
        if not self.model_config_id.strip():
            raise ValueError("response model_config_id must not be blank")

    @classmethod
    def from_contract_response(cls, response: ModelResponse) -> CanonicalModelResponse:
        finish_reason = ModelFinishReason.UNKNOWN
        structured_output: JsonValue = None
        tool_calls: tuple[ModelToolCallRequest, ...] = ()
        latency_ms: float | None = None
        started_unix_ms: float | None = None
        ended_unix_ms: float | None = None

        for metadata in response.adapter_metadata:
            values = metadata.values
            raw_finish = values.get("finish_reason")
            if isinstance(raw_finish, str):
                try:
                    finish_reason = ModelFinishReason(raw_finish)
                except ValueError:
                    finish_reason = ModelFinishReason.UNKNOWN
            latency_ms = _optional_number(values.get("latency_ms"), latency_ms)
            started_unix_ms = _optional_number(
                values.get("started_unix_ms"),
                started_unix_ms,
            )
            ended_unix_ms = _optional_number(values.get("ended_unix_ms"), ended_unix_ms)

            if metadata.namespace == "model-protocol":
                if "structured_output" in values:
                    structured_output = values["structured_output"]
                if "tool_calls" in values:
                    tool_calls = _tool_calls(values["tool_calls"])

        content: tuple[ModelContentBlock, ...] = ()
        if response.text:
            content = (ModelContentBlock(ModelContentKind.TEXT, text=response.text),)

        return cls(
            request_id=response.request_id,
            model_config_id=response.model_ref,
            content=content,
            tool_calls=tool_calls,
            structured_output=structured_output,
            finish_reason=finish_reason,
            usage=dict(response.usage),
            timing=ModelTiming(
                latency_ms=latency_ms,
                started_unix_ms=started_unix_ms,
                ended_unix_ms=ended_unix_ms,
            ),
        )


def _message_text(message: ModelMessage) -> str:
    text_parts = [block.text for block in message.content if block.text is not None]
    if text_parts:
        return "\n".join(text_parts)
    return f"[{message.role.value} content]"


def _optional_number(value: JsonValue | None, current: float | None) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return current


def _tool_calls(value: JsonValue) -> tuple[ModelToolCallRequest, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[ModelToolCallRequest] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        call_id = item.get("call_id")
        tool_name = item.get("tool_name")
        arguments = item.get("arguments")
        if not isinstance(call_id, str) or not isinstance(tool_name, str):
            continue
        if not isinstance(arguments, dict):
            continue
        parsed.append(
            ModelToolCallRequest(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        )
    return tuple(parsed)
