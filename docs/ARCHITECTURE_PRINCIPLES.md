# Architecture Principles

These principles are binding unless changed by an explicit architecture decision.

## 1. Platform-owned canonical model

Canonical public entities and lifecycle semantics belong to AI Multi-Agent Platform, not to an upstream framework. Adapters translate between platform contracts and backend-specific representations.

## 2. Replaceability by contract

Orchestrators, executors, model providers, tool providers, memory/file/knowledge backends, permission services and compute workers must be replaceable behind versioned contracts.

A concrete integration must not require unrelated platform modules to import its private types.

## 3. No privileged upstream framework

Hermes may be the first orchestrator. Forge may provide execution or lifecycle capabilities. LiteLLM may provide model routing. MCP may provide tool interoperability. None of them is the platform itself.

The core remains testable without those systems installed or running.

## 4. Task-centric execution

The primary unit of work is a canonical Task and its Runs, not a chat session. Interactive chat may create or inspect work, but chat history is not the canonical lifecycle record.

## 5. Single-node is a special case of multi-node

Local-only and distributed operation share the same Node, Worker and Job concepts. Scheduling is capability-based rather than tied to hostnames, vendors, VPS classes or operating locations.

## 6. Explicit ownership of durable state

For every lifecycle state there is one canonical owner. New workflow engines or execution backends must not introduce competing lifecycle authorities.

## 7. API-first boundaries

Frontend, CLI, automations and external applications communicate through canonical platform APIs and events. They do not directly depend on Hermes, Forge, model providers or other backend implementations.

## 8. Local-first baseline

The baseline runs self-hosted without mandatory recurring paid AI/API services. Optional external providers remain optional adapters.

## 9. Capability discovery over hard-coded identity

Agents and schedulers should select models, tools, workers and nodes from declared capabilities and policy where practical instead of binding to concrete names in core logic.

## 10. Permissions and approvals are cross-cutting

Identity, permissions, approval gates and auditability belong at canonical platform boundaries rather than only inside individual integrations.

## 11. Provenance and licensing are first-class

Any reused or vendored upstream code retains provenance and compatible licensing. Dependencies, external services, protocols and vendored code are treated as distinct integration categories.

## 12. Evidence-driven evolution

Major architectural dependencies require contract tests, reference implementations and, where useful, isolated evaluation work before becoming production assumptions. Existing work is reused only when it satisfies the new contracts.
