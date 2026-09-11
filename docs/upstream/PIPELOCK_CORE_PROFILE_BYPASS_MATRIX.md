# Pipelock Core protected-profile bypass matrix

Issue #730 requires bypass-resistance claims to be limited to execution profiles whose network boundary
is actually controlled. This document records the distinction between **mediated traffic** and a
**protected execution profile**.

## Rule

Running Pipelock does not automatically redirect or contain unrelated sockets opened by the platform,
an adapter, a browser/network client or a child process. A path is therefore called protected only when
an independent deployment-owned execution/network boundary prevents alternate direct egress.

## Profile matrix

| Profile / surface | Live result | #730 classification |
| --- | --- | --- |
| MCP stdio child beneath `pipelock mcp proxy`, no containment | Child-owned direct socket reaches controlled target | **unsupported as protected** |
| MCP stdio child + #730 INET seccomp evaluation boundary | MCP stdio remains usable; fresh IPv4/IPv6 sockets fail with `EPERM` | **evaluation-only protected profile** |
| In-process ordinary HTTP client | Direct request reaches controlled target while Pipelock is running and no matching target marker is emitted by Pipelock | **unsupported as protected** |
| In-process MCP-HTTP-shaped request | Direct POST reaches controlled target while Pipelock is running | **unsupported as protected** |
| In-process WebSocket client | Direct WebSocket reaches controlled target while Pipelock is running | **unsupported as protected** |
| Direct redirect/alternate endpoint | Direct client follows the alternate target while Pipelock is running | **unsupported as protected** |
| Current Hermes API adapter transport | `UrllibHermesHttpTransport` reaches configured Hermes HTTP target directly while Pipelock is running | **unsupported as protected** |

The direct-path tests intentionally keep the exact pinned Pipelock Core process running at the same
time. They do not route requests through the Pipelock endpoint, and the controlled upstream observes
the requests while Pipelock's log does not contain the evaluation markers. This is the bypass being
measured: the process has an alternate network path that the mediator cannot see.

## Relation to mediated security tests

This classification does not invalidate the previously retained tests showing that requests which are
actually sent through Pipelock receive SSRF/DLP/response/tool inspection and receipt behavior. It
shows the separate requirement needed before those controls can be called complete mediation for an
execution profile.

For example:

- Pipelock can reject a mediated redirect to a private target, but an unconstrained client can still
  make its own direct request unless the deployment prevents that alternate route.
- Pipelock can inspect its generic WebSocket proxy, but an unconstrained WebSocket client can connect
  around it.
- Pipelock can wrap MCP stdio, but the wrapped child can create a second socket unless a separate
  containment boundary denies that operation.
- The current Hermes adapter is a direct HTTP API client; its existence does not itself imply Pipelock
  mediation.

## Production implication

The evaluation-only seccomp helper proves one narrow Linux x86-64 mechanism for an MCP stdio child.
It is not promoted as the platform-wide production sandbox. Production-grade protected profiles need
a deployment-owned mechanism capable of controlling the complete process/network boundary, including
inherited file descriptors, namespace/routing policy and every executable that can initiate egress.

Until such a boundary exists for a surface, #730 must report that surface as **unsupported for a
protected-profile enforcement claim**, not as silently protected by Pipelock.

This result supports Pipelock as an optional mediated enforcement/evidence component, but it rules out
using Pipelock proxy presence alone as proof of network containment.
