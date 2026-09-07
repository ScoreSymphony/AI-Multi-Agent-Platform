# Self-hosted network MessageTransport

Issue #388 adds a dependency-free network-capable implementation of the existing #35
`MessageTransport` contract. It exists to let canonical Worker transport cross process and host
boundaries without making a broker product, cloud provider, VPS class or orchestration system
part of platform identity.

## Components

`TcpMessageBroker` is a small self-hosted delivery boundary. It owns transport-only queue,
consumer-group, retry and dead-letter state by composing the existing deterministic
`InProcessMessageTransport`. It does not own canonical Task, Run, Event, Node, Worker, Workspace
or Artifact state.

`TcpMessageTransport` is the client-side #35 adapter used by Control Plane and Worker processes.
`TransportWorkerDispatcher` and `WorkerTransportEndpoint` remain the canonical distributed Worker
command/reply adapters. Replacing this TCP implementation with another conforming #35 adapter must
not change Worker or Task/Run identity.

## Network exposure

The reference defaults to `127.0.0.1` with an ephemeral port for local development and
multi-process tests. Loopback operation may run without TLS because traffic does not leave the
host.

A non-loopback listener is rejected unless a TLS server context is supplied. A non-loopback
client is likewise rejected unless a TLS client context is supplied. Remote transport should be
bound only to a private operator-controlled network or protected private overlay; this reference
is not intended to expose a raw broker port directly to the public Internet.

For a two-machine deployment the minimum flow is:

```text
Control Plane process ---- authenticated TLS ----+
                                                  |
                                                  v
                                         TcpMessageBroker
                                                  ^
                                                  |
Remote Worker process ---- authenticated TLS ----+
```

The reverse proxy used for the public Control Plane HTTP surface is separate. Worker transport
ports remain internal/private by default.

## Service authentication

Remote operation supports two service-identity patterns without changing the wire envelope:

1. TLS with client-certificate verification (mTLS); or
2. TLS plus a runtime-only pre-shared authentication key.

With a pre-shared key, each initial request contains a fresh nonce, UTC issuance timestamp and an
HMAC-SHA256 proof over the canonical request bytes. The raw key is not sent across the wire and is
never copied into `TransportEnvelope`. The broker rejects stale proofs and nonce replay inside the
configured authentication window.

A production deployment must load the TLS key material and optional HMAC credential from an
operator-managed secret source. Checked-in deployment profiles contain only secret references,
never secret values. Logging and error responses must not include the authentication key.

## Delivery semantics

The adapter preserves the #35 baseline rather than introducing stronger claims:

- at-least-once delivery;
- duplicates are possible;
- ordering is scoped to topic + consumer group;
- one in-flight delivery per reference consumer group;
- explicit ACK/NACK;
- bounded retry and dead-letter handling;
- bounded retained queues and bounded network frames;
- canonical correlation, causation, trace and idempotency fields are preserved;
- broker connection/session identifiers stay transport-private.

`worker_job_id` remains the canonical remote execution identity. A reconnect or replacement
network connection does not allocate a new Worker or Worker Job identity.

## Failure and reconnect behavior

Connection failures map to canonical retryable `ErrorCode.UNAVAILABLE`; operation timeout maps to
`ErrorCode.TIMEOUT`. TLS peer-verification failure maps to `ErrorCode.UNAUTHORIZED`. The client
provider descriptor changes to unavailable after detected network failure and returns to healthy
after a successful connection.

A Worker-side subscription automatically retries a short bounded reconnect sequence for retryable
connection failures. When a Worker process disappears abruptly, the broker detects the closed TCP
stream while waiting for work, closes the underlying #35 subscription, and releases that consumer
group. A restarted Worker can therefore subscribe again with the same canonical Worker identity.

The distributed registry/heartbeat layer remains responsible for deciding whether a Worker is
online, degraded or offline. Transport reconnect never fabricates Worker health or successful
execution.

## Backpressure and limits

`TcpMessageBroker(max_queue_size=...)` delegates retained-queue bounds to the #35 reference
transport. Full queues fail explicitly with retryable `ErrorCode.RESOURCE_EXHAUSTED`; messages are
not silently dropped.

`max_frame_bytes` bounds every JSON-line frame. Oversized or malformed frames fail instead of being
accepted into unbounded memory. Operators should set queue/frame limits together with Worker
concurrency and resource policy rather than treating the broker as an unlimited durable store.

The broker is not canonical persistence. Restarting it can lose transport-only pending delivery
state; canonical reconciliation must recover from Task/Run/Worker state rather than trusting a
broker queue as history.

## Readiness

`TcpMessageTransport.check_ready()` performs a side-effect-free broker ping. Deployment readiness
may use that probe in addition to canonical Worker registration and heartbeat health. A successful
transport ping alone does not mean a Worker is schedulable.

## Least privilege

Run the broker and Worker under dedicated unprivileged process identities. Worker filesystem
access should be limited to its machine-local workspace root and explicitly configured executor
resources. TLS private keys and transport credentials should be readable only by the service that
needs them. Do not mount Control Plane data directories into remote Workers merely to make paths
line up; #37 Workspace references/materialization own that boundary.

## Reference scope

This adapter is intentionally small and dependency-free so the advanced self-hosted deployment has
no paid-service requirement. It is not a permanent broker selection. Redis, NATS, RabbitMQ, Kafka
or another implementation may later satisfy the same #35 contract without becoming canonical
architecture.

The current reference does not provision certificates, DNS, firewall policy, service discovery or
external durable broker storage. Those remain deployment/operator responsibilities and must be
composed through provider-neutral configuration.
