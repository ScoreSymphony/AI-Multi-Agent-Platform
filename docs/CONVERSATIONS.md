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
- references to canonical Tasks, Runs, Artifacts and Results;
- audit-safe metadata and timestamps.

Task, Run, Artifact and Result links are stored as stable canonical IDs. In particular,
`result.attached` is materialized into `Conversation.result_ids` exactly like Run and
Artifact linkage rather than existing only as transient chat presentation state.

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

Conversational generation is also replaceable. The reference single-node composition
wraps `ModelRuntimeConversationResponseProvider` with
`ContextResolvingConversationResponseProvider` when canonical model runtime support is
configured. Agent and AgentTeam targets are resolved to immutable canonical revisions;
model selection then flows through the platform ModelRuntime/router contracts rather
than a provider-private chat session. File and Knowledge context is resolved ephemerally
through the configured canonical providers before the replaceable responder is invoked.

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
3. if the Conversation has a canonical default Agent/AgentTeam and the Task request does
   not already contain an assignment, copies that exact pinned ID/revision into the
   canonical Task `agent_assignment`;
4. calls the canonical Task creation path;
5. validates the returned canonical `task_*` identity;
6. stores only Task references on the message/conversation.

When an Agent or AgentTeam target/default selection omits a revision at Conversation
creation, the Control Plane resolves the current canonical revision once and persists the
exact revision in the Conversation. Existing conversations therefore do not silently
follow later Agent/Team revisions.

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

A canonical `task.waiting` event is projected into the Conversation stream with a
structured `attention` object. The object can include the Task ID, blocked flag, reason
and verification-related identifiers. It is presentation metadata derived from the
canonical event; the Chat layer does not resume the Task automatically.

## Live Task/Run event projection

The Conversation shell projects the existing canonical Task event streams instead of
creating chat-specific execution state. Clients can subscribe through:

`GET /api/v1/conversations/{conversation_id}/events/stream`

The SSE projection:

1. authorizes the Conversation through the existing authenticated Control Plane boundary;
2. reads the Conversation's durable `task_ids` links;
3. subscribes to each canonical Task stream through `subscribe_task_events(...)`;
4. multiplexes canonical `task.*`, `run.*`, `artifact.attached` and `result.attached`
   events into one Conversation stream;
5. keeps the original canonical event unchanged inside the projection envelope;
6. marks the projection as `authoritative: true` because lifecycle truth remains the
   canonical Task/Run event stream;
7. exposes structured canonical `references` on lifecycle events;
8. materializes newly observed Run, Artifact and Result IDs back into durable Conversation
   context without taking ownership of their lifecycle.

Reconnect state is represented by one opaque provider-neutral Conversation cursor. The
cursor contains only per-Task canonical `event_*` positions and can be supplied with
`after_event_id` or the standard `Last-Event-ID` header. It stores no model-provider,
orchestrator, execution-backend or private session identity. Cursors that claim positions
for Tasks not linked to the Conversation are rejected.

Conversation SSE is composed above the current public Notification/Plugin/Automation/
Terminal ASGI stack. Existing lifespan handling, terminal WebSockets and authenticated
stream preparation therefore remain intact rather than being reimplemented by #72.

## Approval and input attention

Chat does not implement a second Approval lifecycle. Approval requests are projected
through the platform's canonical Notification/Approval integration. The browser follows
the canonical Notification API's opaque pagination cursors across the complete authorized
inventory and selects `approval` and `agent_input` notifications whose canonical
`task_id` belongs to the current Conversation. Relevant active requests therefore cannot
disappear merely because they are older than the newest 100 notifications.

Approval cards link to the existing canonical Approval route and use only safe northbound
Notification fields/actions. Proposed privileged payloads are not copied into Conversation
state. Notification live events refresh this attention view, while the Approval domain
remains authoritative for pending/resolved state and authorization.

Together with structured `task.waiting` projections, this gives Chat provider-neutral
visibility into both explicit input requirements and approval requirements without
interpreting model text as an action request.

## Assistant/Agent response streaming

Conversational model/agent output uses a second, deliberately non-authoritative stream:

`POST /api/v1/conversation-messages/{message_id}/response/stream`

The request addresses one already-durable authenticated user Message and requires an
idempotency key. A replaceable `ConversationResponseProvider` receives only canonical
Conversation history, target identity, the authenticated canonical `OperationContext`
and provider-neutral routing preferences. No Hermes, Forge or model-provider session
object is part of the public contract.

For the reference runtime, `ModelRuntimeConversationResponseProvider` resolves Agent and
AgentTeam targets through `AgentService`, including exact selected revisions, applies the
Agent routing profile/fallback policy plus any explicit Conversation model preference,
and submits a canonical model request through `ModelRuntime`. The authenticated owner,
Project, correlation, causation and idempotency-control context is preserved into that
request. Project and Task targets remain provider-neutral context. No provider-native
conversation/session identifier is persisted as platform state.

During generation the server emits:

- `conversation.response.delta` for text chunks;
- `conversation.response.activity` for policy-allowed activity summaries.

Both are explicitly `tentative: true` and `authoritative: false`. They are presentation
state only and cannot queue/start/resume Tasks, invoke privileged tools or mutate the
canonical lifecycle.

The Conversation response contract is chunk/SSE-capable and replacement providers may
emit multiple chunks. The current reference `ModelRuntime` adapter still falls back to the
whole-response `generate` seam because the canonical ModelProvider runtime does not yet
expose native provider streaming. Native provider streaming is owned by #10; #72 does not
create a provider-specific streaming/session API to work around that missing lower-level
seam.

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

At response time, File and Knowledge references are re-authorized under the current
canonical `OperationContext` and materialized only into ephemeral responder context.
Textual File content is bounded to 128 KiB per File; non-text/binary Files contribute
safe metadata rather than arbitrary binary prompt data. Knowledge retrieval is
provider-neutral, restricted to the referenced source, bounded to five results and 64 KiB
of materialized text. The resolved source content is never written back into Conversation
history.

Conversation state stores only stable canonical source identities. It does **not** copy
File bytes, Knowledge document text, embeddings, provider-native index identifiers or
internal retrieval state into chat history. The implementation is covered against local
and replacement provider contracts so the Conversation domain does not depend on SQLite
or private provider methods.

## Retention, deletion and export

Conversation retention is independent from canonical Task/Run event retention. A
Conversation can use durable or time-bounded retention policy metadata managed by the
platform; northbound clients cannot write the reserved retention fields directly.

Deleting a Conversation tombstones and redacts Conversation-owned message history. It
does not delete Tasks, Runs, Artifacts, Results or their canonical event history. Portable
export returns Conversation-owned state plus canonical references without recursively
copying referenced resources or provider-private data.

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
- inline canonical Task/Run/Artifact/Result lifecycle references;
- structured waiting/input attention from authoritative Task events;
- canonical Approval and Agent-input notification attention for linked Tasks;
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
command. Approval notifications only project already-authorized canonical attention;
Chat cannot create an alternate approval decision path.

## Acceptance coverage

The #72 implementation covers:

- canonical Conversation/Message contracts;
- Agent/AgentTeam targets with exact, creation-time-pinned canonical revisions;
- Project, Task and canonical orchestrator targeting;
- provider-neutral model-routing preferences, Agent routing profiles/fallback and canonical
  `ModelRuntime` generation;
- authenticated canonical OperationContext propagation into model and attachment providers;
- typed File/Artifact/Knowledge/Task/Run/Result/Agent/Team references;
- response-time authorized, bounded, ephemeral File/Knowledge context materialization;
- durable restart-safe Conversation persistence and paginated history;
- Control Plane resources, commands, HTTP routes, authentication and authorization;
- authenticated sender binding and cross-project/private isolation;
- canonical File attachment authorization;
- canonical `knowledge_source_*` references through a replaceable `KnowledgeProvider`;
- Task creation/attachment linkage with exact Agent/Team assignment and no second task engine;
- durable Run, Artifact and Result linkage projected from canonical lifecycle events;
- normalized, restart-safe `Conversation.task_ids` linkage;
- explicit waiting-Task input/resume with canonical provenance and kernel lifecycle;
- structured `task.waiting` attention without implicit resume behavior;
- canonical Approval/Agent-input attention through complete Notification pagination;
- provider-neutral authoritative Task/Run SSE with opaque multi-Task reconnect cursors;
- provider-neutral tentative Assistant/Agent response streaming with durable final commit;
- response idempotency and replacement-provider tests with no private-session leakage;
- retention, time-bounded policy, tombstone/deletion and portable export behavior;
- frontend multi-conversation Chat area with explicit Task bridge, lifecycle references,
  approval/input attention and live response UI;
- standard single-node Conversation activation and restart persistence;
- compatibility with current authentication, authorization, Notification, Plugin,
  Automation, Terminal, Verification, Hermes and Forge composition boundaries.

Durable work therefore remains represented by canonical Task/Run/Artifact/Result/Event
contracts even when the user enters the platform through Chat.
