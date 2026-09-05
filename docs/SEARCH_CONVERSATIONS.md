# Conversation and Message Search

Issue: #289  
Canonical Conversation domain: #72  
Canonical global Search foundation: #45

## Purpose

Global Search may discover retained canonical Conversations and Messages, but Search is
never Conversation history, Memory, or lifecycle/Event authority. Every Search document
is derived and rebuildable from the canonical #72 repository.

## Indexability policy

A Conversation is eligible for Search only while its canonical state is not tombstoned
and its retention policy has not expired. Open and archived Conversations remain eligible
while retained.

A Message is eligible only while its parent Conversation is eligible and the canonical
Message is not tombstoned. Search never turns Message content into Memory or Knowledge.

The correctness-first Search path rebuilds from these canonical resources, so a retention
change, individual Message tombstone, Conversation expiry, or Conversation deletion
removes the resource from the next derived rebuild rather than preserving stale chat text
as Search-owned state.

## Safe Search projection

Conversation Search documents contain only:

- canonical Conversation ID and type;
- title and optional summary;
- canonical owner, Project and Workspace scope;
- canonical Conversation status;
- canonical timestamps.

Message Search documents contain only:

- canonical Message ID and type;
- parent Conversation ID;
- role and revision;
- parent owner, Project and Workspace scope;
- canonical Message status and timestamps;
- a bounded snippet derived only from canonical text/Markdown content blocks.

The Message Search projection deliberately excludes JSON content blocks, attachment and
reference payloads, arbitrary Conversation/Message metadata, model routing metadata,
provider-native/session identifiers, correlation/causation fields, and other backend
state. Provider-native chat/session IDs therefore never become canonical Search identity.

## Authorization and non-disclosure

Conversation Search hits are re-authorized against current canonical #72 state before
they become caller-visible. The implementation reloads the canonical Conversation (and,
for Message hits, the canonical Message and parent Conversation) instead of trusting
owner/Project values copied into the derived Search document.

Private Conversations preserve #72's exact owner rule: a private Conversation is visible
only to the principal identified by its canonical `owner_ref`. Project-scoped resources
then pass through the existing platform authorization boundary. This check occurs before
Search results or exact-ID existence are returned, preserving the #45 non-disclosure
contract for result items, counts and snippets.

## Identity and canonical references

Search uses the existing #45 canonical identity rules:

- `conversation_*` remains the Conversation identity;
- `message_*` remains the Message identity;
- Search results point to `/api/v1/conversations/{id}` or
  `/api/v1/conversation-messages/{id}`;
- Search provider/index identifiers are never resource identity.

## Rebuild and replaceability

The integration uses the existing registered-resource Search rebuild seam. It does not
add a chat-specific index, vector database, embedding requirement, or paid service. A
replacement `SearchProvider` receives the same backend-neutral `SearchDocument` values
and can rebuild entirely from canonical #72 resources.

## Explicit separation from Memory and Events

Conversation Search is discovery over retained chat resources only. It does not:

- promote Message text into Memory or Knowledge;
- copy canonical Task/Run lifecycle Events into Conversation history;
- make Search status authoritative over Conversation retention/deletion;
- authorize operations from Search-document metadata;
- preserve deleted chat text after the canonical retention policy removes it.
