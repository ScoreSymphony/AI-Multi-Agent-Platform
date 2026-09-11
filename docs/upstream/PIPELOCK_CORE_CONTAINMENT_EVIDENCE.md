# Pipelock Core containment and bypass evidence

Issue #730 evaluates Pipelock Core as an optional enforcement/evidence adapter. This document records
the narrow process-containment experiment used to distinguish **Pipelock mediation** from an actual
**network security boundary**.

## Invariant

Pipelock remains downstream of the canonical #15 authorization and #591 egress-policy decisions. A
Pipelock proxy is not treated as proof that an execution profile cannot open a second, unmediated
network path.

## Evaluation profile

The live fixture runs the exact pinned, tag-free Pipelock Core candidate and wraps a deterministic MCP
stdio server through `pipelock mcp proxy -- ...`.

Two variants are compared against the same controlled loopback target:

1. **Uncontained child** — the MCP stdio server is launched directly beneath Pipelock. Its evaluation
   tool attempts a direct socket that does not traverse a Pipelock HTTP/WebSocket endpoint.
2. **Evaluation-contained child** — the same MCP server is launched through
   `scripts/ci/issue502_no_network_exec.py`. That existing Linux x86-64 seccomp helper installs
   `PR_SET_NO_NEW_PRIVS` plus a syscall filter denying socket creation and principal socket I/O calls,
   then execs the MCP child. The filter is inherited by descendants.

The contained variant also performs an ordinary MCP stdio `lookup` call. This ensures that a result of
"network blocked" is not caused by breaking the stdio transport itself.

The direct-network probe covers four request shapes from the MCP child:

- raw TCP;
- HTTP request framing;
- MCP-over-HTTP request framing;
- WebSocket-upgrade request framing.

All four use the same underlying direct socket boundary intentionally: if socket creation is denied,
these child-owned alternate egress paths cannot silently bypass the mediator.

## Required interpretation

The experiment has three meaningful outcomes:

- If the **uncontained** child reaches the controlled target, Pipelock MCP proxying alone is not a
  complete network-containment boundary.
- If the **contained** child receives `EPERM` for direct sockets while MCP stdio still works, the test
  demonstrates an evaluation-only profile in which child-owned direct egress is blocked separately
  from Pipelock mediation.
- If either condition is not reproducible, #730 must not claim bypass resistance for MCP stdio.

## Scope limit

The reused #502 seccomp helper is intentionally narrow evaluation infrastructure, not a generic
production sandbox. The result therefore must **not** be generalized into a production containment
claim for every agent, browser, connector, Hermes/tool, MCP HTTP or WebSocket execution profile.
Those profiles require their own deployment-owned OS/container/network boundary before they can be
called protected against direct-network bypass.

This distinction is material to the final #730 recommendation: Pipelock may add inspection,
enforcement and receipts on mediated traffic, but complete mediation still depends on an independent
execution-containment mechanism.
