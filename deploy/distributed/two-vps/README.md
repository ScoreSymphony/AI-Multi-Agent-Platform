# Two-VPS private-tunnel reference topology

Issue: #562

This directory contains a credential-free **deployment reference**, not a new platform contract.
The canonical #14 Node/Worker/scheduler path, #35 transport, #36 service authentication, #15
authorization and #240 distributed composition remain authoritative.

The reference uses WireGuard because it is self-hosted and easy to reproduce. WireGuard is not a
canonical dependency: another private tunnel may be substituted when it preserves the same network
and security properties.

## Reference roles

| Host | Deployment role | Tunnel address used in examples | Public exposure |
| --- | --- | --- | --- |
| Host A | Control Plane, scheduler, Worker protocol HTTPS edge, message broker | `10.203.0.1/30` | deliberate client/TLS edge and WireGuard handshake only |
| Host B | remote canonical Node/Worker | `10.203.0.2/30` | no platform Worker/control-plane service required publicly |

The `10.203.0.0/30` values are documentation-only private addresses. Provider IDs, VPS names,
public addresses and tunnel peer identities remain deployment metadata and must never become
canonical Node/Worker/Task/Run identity.

## Files

- `wireguard/host-a.conf.example` — Host A listener; contains placeholders only.
- `wireguard/host-b.conf.example` — Host B initiator with bounded keepalive; contains placeholders
  only.
- `host-a.env.example` / `host-b.env.example` — non-secret service wiring examples. Secret values
  are referenced through runtime files/environment and are deliberately absent.

The complete operator procedure, firewall matrix, failure/recovery phases and evidence commands are
in [`docs/operations/TWO_VPS_PRIVATE_TUNNEL_ACCEPTANCE.md`](../../../docs/operations/TWO_VPS_PRIVATE_TUNNEL_ACCEPTANCE.md).

## Network invariant

```text
client (optional public TLS edge)
          |
          v
Host A Control Plane / scheduler
          |
          | private tunnel only
          +---- Worker protocol HTTPS ----+
          |                               |
          +---- #35 message transport ----+----> Host B canonical Worker
```

Tunnel encryption does not replace platform/service identity. The remote reporter still uses the
#36 Worker credential and #15 authorization. The message transport still uses its supported TLS +
HMAC and/or mTLS mode.
