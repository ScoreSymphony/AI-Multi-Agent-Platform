# Conversation interaction shell

Issue: #72

## Purpose

The conversation domain provides a durable user-facing interaction shell over canonical
platform resources. A conversation can contain user/agent messages, selected Agent or
AgentTeam references, model-routing preferences, file/artifact/knowledge references and
links to Tasks/Runs/results.

Conversation state is **not** execution state. Tasks and Runs remain owned by the
canonical Task/Run lifecycle and are only referenced from conversations/messages.
Backend-private model sessions are never canonical conversation identity.

## Canonical resources

### Conversation

A `Conversation` owns interaction metadata only:

- canonical `conversation_*` identity;
- title/summary and owner reference;
- optional Project/Workspace context;
- participants;
- open/archive/tombstone status;
- optional canonical Agent/AgentTeam selection;
- optional provider-neutral model-routing preference;
- references to canonical Tasks, Runs and Artifacts;
- audit-safe metadata and timestamps.

### ConversationMessage

A `ConversationMessage` owns durable message history:

- canonical `message_*` identity and parent Conversation;
- sender and role;
- typed content blocks;
- canonical file/artifact/knowledge/task/run/result/agent/team references;
- canonical model configuration/provider attribution when available;
- timestamps, revision/tombstone fields and correlation/causation identifiers.

Message content is stored as data. Markdown or text does not constitute authorization to
invoke tools, create Tasks, resume Tasks or perform privileged capabilities.

## Persistence

`JsonConversationRepository` is the first platform-owned durable reference
implementation. It persists only canonical conversation/message resources to one
versioned JSON document and updates it atomically with `os.replace`.

The repository intentionally contains no model-provider session state and no mirrored
Task/Run lifecycle fields. It exists as a deterministic reference implementation; a
future storage adapter may replace it behind `ConversationRepository` without changing
the conversation contracts.

The standard single-node deployment now configures this repository at
`db/conversations.json`. Conversation and Message history therefore survives process
restart in the normal self-hosted profile. The same deployment injects its canonical
Agent service and File provider into the Conversation boundary rather than creating
chat-private copies.

A conversational response provider remains optional and separately replaceable. The
single-node reference profile does not fabricate model output when no canonical model/
responder runtime has been configured.

## Control Plane surface

When `ConversationService` is configured, the composed Control Plane exposes canonical
`conversations`, `conversation-messages` and `conversation-exports` resources plus
ergonomic chat routes. Deployments that intentionally omit Conversation support retain
the existing Control Plane/OpenAPI baseline unchanged.

The current conversation HTTP surface includes:

- `POST /api/v1/conversations`;
- `GET /api/v1/conversations` and canonical resource reads;
- `GET|POST /api/v1/conversations/{conversation_id}/messages`;
- `GET /api/v1/conversations/{conversation_id}/events/stream` (authoritative Task/Run SSE);
- `POST /api/v1/conversation-messages/{message_id}/response/stream` (tentative response SSE);
- `POST /api/v1/conversations/{conversation_id}:archive` / `:reopen`;
- `POST /api/v1/conversations/{conversation_id}:link-run` / `:link-artifact`;
- `POST /api/v1/conversation-messages/{message_id}:create-task`;
- `POST /api/v1/conversation-messages/{message_id}:attach-task`;
- `POST /api/v1/conversation-messages/{message_id}:resume-task`;
- `POST /api/v1/conversations/{conversation_id}:set-retention`;
- `DELETE /api/v1/conversations/{conversation_id}`;
- `GET /api/v1/conversations/{conversation_id}/export`.

The routes remain adapters over canonical Control Plane resource/command seams. They do
not introduce provider-specific chat APIs.

## Task handoff and linkage

`ConversationService.handoff_message_to_task(...)` accepts the existing canonical Task
creator as an injected callable. The service:

1. loads the durable message and conversation;
2. augments Task metadata with `conversation_id` and `conversation_message_id`;
3. calls the canonical Task creation path;
4. validates the returned canonical `task_*` identity;
5. stores only Task references on the message/conversation.

All Task relationships use the same `Conversation.task_ids` invariant. This includes a
Task-targeted conversation, creating a Task from a message and attaching a message to an
existing Task. `ConversationService.link_task(...)` deduplicates the relationship and
persists it across repository restart.

The conversation service never queues, starts, retries, completes or otherwise mirrors
the Task lifecycle.

## Waiting Tasks and explicit user input

Supplying a chat message and resuming work are deliberately separate operations.
Appending an ordinary user message **never** changes canonical Task state, even when the
conversation targets a Task that is currently `waiting`.

To continue a waiting Task, the client must explicitly call:

`POST /api/v1/conversation-messages/{message_id}:resume-task`

with the canonical `task_id`. The Control Plane then:

1. verifies that the referenced message is an authenticated user message;
2. authorizes access to the conversation/message;
3. resolves the canonical Task and enforces the Project boundary;
4. separately authorizes `task:resume` through the existing Task authorization path;
5. links the message and conversation to the Task using canonical references;
6. stores only `conversation_id` and `message_id` as Task input provenance;
7. performs the lifecycle change through `PlatformKernel.resume_task(...)`.

The user's free-text message is not copied into Task metadata. The authoritative lifecycle
transition remains the kernel-owned `task.resumed` event. The command is idempotent and a
first attempt against a Task that is not waiting fails through the canonical lifecycle
conflict path without writing conversation-input metadata.

## Live Task/Run event projection

The Conversation shell projects the existing canonical Task event streams instead of
creating chat-specific execution state. Clients can subscribe through:

`GET /api/v1/conversations/{conversation_id}/events/stream`

The SSE projection:

1. authorizes the Conversation through the existing authenticated Control Plane boundary;
2. reads the Conversation's durable `task_ids` links;
3. subscribes to each canonical Task stream through `subscribe_task_events(...)`;
4. multiplexes canonical `task.*` and `run.*` events into one Conversation stream;
5. keeps the original canonical event unchanged inside the projection envelope;
6. marks the projection as `authoritative: true` because lifecycle truth remains the
   canonical Task/Run event stream.

Reconnect state is represented by one opaque provider-neutral Conversation cursor. The
cursor contains only per-Task canonical `event_*` positions and can be supplied with
`after_event_id` or the standard `Last-Event-ID` header. It stores no model-provider,
orchestrator, execution-backend or private session identity. Cursors that claim positions
for Tasks not linked to the Conversation are rejected.

Conversation SSE is composed above the current public Notification/Plugin/Automation/
Terminal ASGI stack. Existing lifespan handling, terminal WebSockets and authenticated
stream preparation therefore remain intact rather than being reimplemented by #72.

## Assistant/Agent response streaming

Conversational model/agent output uses a second, deliberately non-authoritative stream:

`POST /api/v1/conversation-messages/{message_id}/response/stream`

The request addresses one already-durable authenticated user Message and requires an
idempotency key. A replaceable `ConversationResponseProvider` receives only canonical
Conversation history, target identity and provider-neutral routing preferences. No
Hermes, Forge or model-provider session object is part of the public contract.

During generation the server emits:

- `conversation.response.delta` for text chunks;
- `conversation.response.activity` for policy-allowed activity summaries.

Both are explicitly `tentative: true` and `authoritative: false`. They are presentation
state only and cannot queue/start/resume Tasks, invoke privileged tools or mutate the
canonical lifecycle.

Only after successful completion is one Assistant `ConversationMessage` persisted. The
stream then emits `conversation.response.committed` with `durable: true`. The committed
message remains conversation history rather than Task/Run event truth. If generation
fails before completion, no partial Assistant message is stored. Reusing the same
idempotency key replays the previously committed response instead of invoking the
response provider twice.

The browser Chat client uses the existing authenticated browser-session fetch boundary
for this POST-SSE request, so session/CSRF handling is not bypassed by live response
transport.

## File and Knowledge references

Conversation messages may carry canonical File, Artifact and Knowledge references as
context, but a reference never grants access to the referenced resource.

Files are validated through the configured canonical `FileProvider` and the authenticated
`DataAccessContext`. Knowledge references use `kind: "knowledge"` and must identify a
canonical `knowledge_source_*` resource. They are validated through the public,
replaceable `KnowledgeProvider.get_index_status(...)` boundary with the same authenticated
owner/project context.

Conversation state stores only the stable Knowledge source identity. It does **not** copy
Knowledge document text, embeddings, provider-native index identifiers or internal
retrieval state into chat history. The implementation is covered against both the local
Knowledge provider and a replacement provider so the Conversation domain does not depend
on SQLite or private provider methods.

## Retention, deletion and export

Conversation retention is independent from canonical Task/Run event retention. A
Conversation can use durable or time-bounded retention policy metadata managed by the
platform; northbound clients cannot write the reserved retention fields directly.

Deleting a Conversation tombstones and redacts Conversation-owned message history. It
does not delete Tasks, Runs, Artifacts or their canonical event history. Portable export
returns Conversation-owned state plus canonical references without recursively copying
referenced resources or provider-private data.

This distinction also keeps conversation history separate from long-term memory. Nothing
in #72 automatically promotes a message into memory or Knowledge; a later memory policy
must make such promotion explicit.

## Web Chat area

The reference frontend exposes `/chat` when `conversations` appears in the runtime
manifest. The page provides:

- multiple durable Conversations and target selection;
- Agent, AgentTeam, Project, Task and platform-orchestrator contexts;
- typed canonical reference attachments;
- Conversation history with tombstone-safe rendering;
- tentative live Assistant text/activity separated visually from durable messages;
- authoritative Task/Run activity projected separately from chat output;
- canonical Task/Run/Artifact links;
- explicit create/attach/resume Task controls;
- archive/reopen, export and tombstone actions.

Sending a message first persists the user Message. If a response provider is configured,
the browser then consumes the provider-neutral response stream. Conversation switching
and conflicting mutations are disabled while that response operation is active so a
stream cannot be accidentally rendered into another thread.

## Security boundary

The completed #36 authentication/session contract and existing #15 authorization layer
remain the authoritative identity and permission boundaries. Conversation operations do
not invent a second auth/session model.

Conversation creation, reads, messages and mutations are scoped through existing Control
Plane authorization. Private conversations are owner-bound. Project conversations and
references enforce project isolation. Sender identity comes from the authenticated actor;
northbound clients cannot spoof assistant/tool/system messages as user input.

File and Knowledge access are resolved through their canonical provider boundaries with
the authenticated data access context. Storing a reference in a message never grants
permission to read the referenced content.

Privileged lifecycle operations use their own canonical authorization. In particular,
permission to append a message does not imply `task:resume` permission. Tentative
assistant/model output is never interpreted as an authorization grant or privileged
command.

## Acceptance coverage

The #72 implementation covers:

- canonical Conversation/Message contracts;
- Agent/AgentTeam targets with exact canonical revisions;
- Project, Task and canonical orchestrator targeting;
- provider-neutral model-routing preferences and model/provider validation;
- typed File/Artifact/Knowledge/Task/Run/Result/Agent/Team references;
- durable restart-safe Conversation persistence and paginated history;
- Control Plane resources, commands, HTTP routes, authentication and authorization;
- authenticated sender binding and cross-project/private isolation;
- canonical File attachment authorization;
- canonical `knowledge_source_*` references through a replaceable `KnowledgeProvider`;
- Task creation/attachment linkage without a second task engine;
- Run and Artifact linking;
- normalized, restart-safe `Conversation.task_ids` linkage;
- explicit waiting-Task input/resume with canonical provenance and kernel lifecycle;
- provider-neutral authoritative Task/Run SSE with opaque multi-Task reconnect cursors;
- provider-neutral tentative Assistant/Agent response streaming with durable final commit;
- response idempotency and replacement-provider tests with no private-session leakage;
- retention, time-bounded policy, tombstone/deletion and portable export behavior;
- frontend multi-conversation Chat area with explicit Task bridge and live response UI;
- standard single-node Conversation activation and restart persistence;
- compatibility with current authentication, authorization, Notification, Plugin,
  Automation, Terminal, Verification, Hermes and Forge composition boundaries.

Durable work therefore remains represented by canonical Task/Run/Artifact/Event
contracts even when the user enters the platform through Chat.
