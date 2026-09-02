# Product Vision

## Purpose

AI Multi-Agent Platform is a general-purpose platform for defining, orchestrating, executing, observing and evaluating AI-assisted work across one or many machines.

It is not a ScoreSymphony-specific product. ScoreSymphony or any other domain application may consume the platform through its public APIs and extension mechanisms without becoming part of the platform core.

## Ideal end state

A user can express a goal and have the platform turn it into canonical tasks, plans, steps, runs, artifacts and results. Multiple agents may cooperate dynamically. Models, tools, execution backends and compute nodes can be selected or replaced according to capabilities, policy and configuration.

The same platform should work on a single local computer or across heterogeneous nodes such as desktops, notebooks, workstations, home servers, VPS instances, cloud VMs, GPU servers, NAS devices or other reachable machines. No hardware class is privileged in the domain model.

## Canonical workflow

Goal -> Task -> Plan -> Steps/Subtasks -> Runs -> Artifacts -> Result

The platform owns the canonical identities and lifecycle semantics needed to trace this flow. External frameworks may implement parts of it but must not redefine the public platform model.

## Major product areas

- Home and activity overview
- Chat/interactive goal entry
- Tasks and runs
- Projects
- Agents and agent teams
- Automations and triggers
- Files
- Knowledge
- Memory
- Tools and integrations
- Models and model routing
- Compute: nodes, workers, jobs and resources
- Evaluations
- Events
- Approvals
- Observability
- Identity, permissions and security
- Settings
- Plugin/extension registry
- Import/export and reusable templates

## Replaceable subsystems

The architecture must allow multiple implementations for at least:

- Orchestration
- Execution/lifecycle backends
- Model providers and routers
- Tool/capability providers
- Memory
- Files
- Knowledge
- Event transport/storage
- Authorization
- Node/worker implementations
- Observability backends
- Durable workflow engines, if one is adopted

Hermes, Forge, LiteLLM, MCP implementations and similar projects are candidates for adapters or reusable components, not permanent definitions of these subsystems.

## Deployment and cost principles

The default baseline must be self-hostable and local-first. It must be possible to operate the platform without required recurring paid AI/API services. Optional paid providers may be supported through adapters, but they must never be necessary for baseline operation.

## Product quality goals

The final system should be:

- extensible without repeated core rewrites;
- testable with reference/fake implementations;
- observable end to end;
- recoverable after failures;
- secure by explicit permissions and approvals;
- auditable through canonical events and identifiers;
- portable across deployment environments;
- understandable enough that upstream integrations can be added or removed without losing architectural ownership.
