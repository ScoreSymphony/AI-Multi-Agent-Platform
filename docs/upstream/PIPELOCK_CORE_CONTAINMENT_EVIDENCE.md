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
   `scripts/ci/issue730_inet_socket_deny_exec.py`. This Linux x86-64 seccomp helper installs
   `PR_SET_NO_NEW_PRIVS` and denies creation of fresh `AF_INET` and `AF_INET6` sockets with `EPERM`,
   while leaving Unix-domain sockets and `socketpair()` available so Python asyncio and MCP stdio can
   continue to operate. The filter survives exec and is inherited by descendants.

The contained variant also performs an ordinary MCP stdio `lookup` call. This ensures that a result of
"network blocked" is not caused by breaking the stdio transport itself.

The direct-network probe covers four request shapes from the MCP child:

- raw TCP;
- HTTP request framing;
- MCP-over-HTTP request framing;
- WebSocket-upgrade request framing.

All four use the same underlying direct IPv4 socket boundary intentionally: if fresh INET socket
creation is denied, these child-owned alternate egress paths cannot silently open their own network
connection around the mediator.

## Why the #502 wrapper is not reused directly

The repository's existing `issue502_no_network_exec.py` evaluation helper denies `socketpair()` in
addition to network sockets. A live #730 attempt showed that this also prevents Python asyncio from
constructing its internal self-pipe, so the MCP stdio server cannot start. That result is useful
negative evidence but is not a valid demonstration of a usable protected MCP profile.

The #730 helper is therefore intentionally narrower: it blocks creation of IPv4/IPv6 sockets while
preserving the local Unix-socket primitives needed by the process runtime.

## Required interpretation

The experiment has three meaningful outcomes:

- If the **uncontained** child reaches the controlled target, Pipelock MCP proxying alone is not a
  complete network-containment boundary.
- If the **contained** child receives `EPERM` for fresh IPv4/IPv6 sockets while MCP stdio still works,
  the test demonstrates an evaluation-only profile in which child-owned direct INET egress is blocked
  separately from Pipelock mediation.
- If either condition is not reproducible, #730 must not claim bypass resistance for MCP stdio.

## Scope limit

The #730 seccomp helper is intentionally narrow evaluation infrastructure, not a generic production
sandbox. It assumes unrelated network file descriptors are not passed into the child and proves only
that the exercised Linux x86-64 MCP stdio child cannot create fresh IPv4/IPv6 sockets. It does not
provide namespace isolation, firewall ownership, inherited-FD revocation or a deployment-wide routing
policy.

The result therefore must **not** be generalized into a production containment claim for every agent,
browser, connector, Hermes/tool, MCP HTTP or WebSocket execution profile. Those profiles require
their own deployment-owned OS/container/network boundary before they can be called protected against
direct-network bypass.

This distinction is material to the final #730 recommendation: Pipelock may add inspection,
enforcement and receipts on mediated traffic, but complete mediation still depends on an independent
execution-containment mechanism.
