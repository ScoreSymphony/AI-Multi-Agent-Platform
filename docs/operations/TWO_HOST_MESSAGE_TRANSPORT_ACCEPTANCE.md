# Real two-host MessageTransport acceptance

Issue: #388

This runbook closes the evidence gap left after the network-capable #35 transport implementation
landed. It exercises the existing `TcpMessageBroker` / `TcpMessageTransport` adapter and the
canonical `TransportWorkerDispatcher` / `WorkerTransportEndpoint` path on **two independent hosts**.
It does not define a second transport, Worker identity model, scheduler, deployment profile or
Artifact model.

The run is deliberately narrower than #562. #388 proves the real cross-host Worker message path,
authenticated encrypted transport, result retrieval, Artifact/Evidence reference preservation and
Worker process restart/reconnect with stable canonical Worker identity. The broader private-tunnel,
registration/heartbeat, scheduler placement, interruption/reconciliation and exposure checks remain
owned by #562.

## What counts as acceptance

A valid run must satisfy all of the following:

- Host A and Host B are physically/virtually independent machines, not two processes on one host.
- The broker is reached through a non-loopback address.
- TLS peer verification is enabled.
- Service authentication uses either mTLS client identity or TLS plus the runtime HMAC credential.
- Host A dispatches through `TransportWorkerDispatcher`.
- Host B consumes through `WorkerTransportEndpoint` using the same canonical `worker_id` before and
  after an independent Worker-process restart.
- A canonical input Artifact reference, a Worker-produced Artifact reference and Worker Evidence
  references survive result retrieval.
- The same Worker Job is intentionally dispatched twice in each run and returns the same execution
  handle, proving the canonical idempotency path over the real transport.
- The Worker process is stopped and started again between the first and second acceptance run.
- The final restart verifier sees the same `worker_id` but a different process-instance Evidence
  reference.

Same-host socket/process tests remain valuable regression coverage, but they **do not** satisfy the
real two-host criterion in #388.

## Security and evidence rules

Use only an operator-controlled private address or private overlay for the broker listener. Do not
expose the raw broker port publicly merely for acceptance.

Never commit or attach:

- private keys;
- HMAC secrets;
- reusable service credentials;
- public/private host addresses when they are not needed for the issue record;
- certificate key material.

The harness writes only canonical/non-secret IDs, opaque Artifact/Evidence references, generic host
labels and the authentication mode. It deliberately records neither the broker address nor
credential material.

The host labels are evidence labels, not infrastructure identity. Use neutral values such as
`host-a` and `host-b`; VPS/provider identifiers must not become canonical Worker identity.

## Prerequisites

Use the exact same repository commit on both hosts and install the platform from that checkout.
Record the commit SHA separately for the #388 issue comment:

```bash
git rev-parse HEAD
python -m pip install -e ".[dev]"
```

Provision TLS material outside the repository. The examples below use mTLS:

- one CA trusted by the broker for client certificates;
- a broker server certificate valid for the name supplied as `BROKER_TLS_NAME`;
- a Host A client certificate/key;
- a Host B client certificate/key.

The existing `platform-message-broker` entrypoint accepts these files directly. Certificate
provisioning is intentionally outside #388.

Create stable, non-secret canonical IDs once on Host A and copy the values to Host B:

```bash
WORKER_ID="$(python -c 'from ai_multi_agent_platform.domain import new_id; print(new_id("worker"))')"
OUTPUT_ARTIFACT_REF="$(python -c 'from ai_multi_agent_platform.domain import new_id; print(new_id("artifact"))')"
printf 'WORKER_ID=%s\nOUTPUT_ARTIFACT_REF=%s\n' "$WORKER_ID" "$OUTPUT_ARTIFACT_REF"
```

Do **not** regenerate `WORKER_ID` for the restart phase.

## 1. Start the TLS broker on Host A

Use the private/non-loopback bind address for `BROKER_PRIVATE_BIND` and the certificate identity for
`BROKER_TLS_NAME`:

```bash
platform-message-broker \
  --host "$BROKER_PRIVATE_BIND" \
  --port 8765 \
  --cert-file "$BROKER_SERVER_CERT" \
  --key-file "$BROKER_SERVER_KEY" \
  --client-ca-file "$CLIENT_CA_CERT"
```

The server will fail closed if a non-loopback listener has no TLS context. With
`--client-ca-file`, the broker requires a verified client certificate.

## 2. Start the canonical Worker endpoint on Host B

Use the stable `WORKER_ID` and output Artifact reference created above:

```bash
python scripts/acceptance/two_host_message_transport.py worker \
  --broker-host "$HOST_A_PRIVATE_ADDRESS" \
  --broker-port 8765 \
  --ca-file "$BROKER_CA_CERT" \
  --server-hostname "$BROKER_TLS_NAME" \
  --cert-file "$HOST_B_CLIENT_CERT" \
  --key-file "$HOST_B_CLIENT_KEY" \
  --worker-id "$WORKER_ID" \
  --worker-host-label host-b \
  --output-artifact-ref "$OUTPUT_ARTIFACT_REF"
```

A successful startup prints one sanitized JSON line containing `status=ready`, the canonical
`worker_id`, a per-process `worker_instance_ref`, the generic Worker host label and the transport
authentication mode. The process-instance reference is Evidence only; it never replaces the
canonical Worker ID.

## 3. Dispatch and retrieve the first result from Host A

Run the control side while the Host B Worker is active:

```bash
python scripts/acceptance/two_host_message_transport.py control \
  --broker-host "$HOST_A_PRIVATE_ADDRESS" \
  --broker-port 8765 \
  --ca-file "$BROKER_CA_CERT" \
  --server-hostname "$BROKER_TLS_NAME" \
  --cert-file "$HOST_A_CLIENT_CERT" \
  --key-file "$HOST_A_CLIENT_KEY" \
  --worker-id "$WORKER_ID" \
  --control-host-label host-a \
  --expected-worker-host-label host-b \
  --output-artifact-ref "$OUTPUT_ARTIFACT_REF" \
  --json-report issue388-first.json
```

The command fails unless all of these are observed through the real transport:

- encrypted + authenticated broker connection;
- successful canonical dispatch;
- repeated dispatch returns the same execution handle;
- result comes from the requested canonical Worker;
- input Artifact reference returns;
- Worker-produced output Artifact reference returns;
- acceptance Evidence reference returns;
- the per-process Worker Evidence reference returns;
- the Worker-reported host label is `host-b`.

## 4. Restart only the Worker process

Stop the Host B acceptance Worker. Keep the broker and Host A checkout unchanged. Start the Worker
again using the **same command, same `WORKER_ID` and same `OUTPUT_ARTIFACT_REF`**.

The new Worker process prints a different `worker_instance_ref`. That difference is expected and is
used to prove that a process restart occurred without changing canonical Worker identity.

## 5. Dispatch and retrieve the second result

Repeat the Host A control command with a second report destination:

```bash
python scripts/acceptance/two_host_message_transport.py control \
  --broker-host "$HOST_A_PRIVATE_ADDRESS" \
  --broker-port 8765 \
  --ca-file "$BROKER_CA_CERT" \
  --server-hostname "$BROKER_TLS_NAME" \
  --cert-file "$HOST_A_CLIENT_CERT" \
  --key-file "$HOST_A_CLIENT_KEY" \
  --worker-id "$WORKER_ID" \
  --control-host-label host-a \
  --expected-worker-host-label host-b \
  --output-artifact-ref "$OUTPUT_ARTIFACT_REF" \
  --json-report issue388-second.json
```

## 6. Verify restart/reconnect evidence

On Host A:

```bash
python scripts/acceptance/two_host_message_transport.py verify-restart \
  issue388-first.json \
  issue388-second.json \
  --json-report issue388-restart.json
```

The verifier fails unless both reports show:

- passing TLS + service-authenticated transport;
- the same canonical `worker_id`;
- distinct process-instance Evidence references;
- the same distinct `host-a` / `host-b` labels;
- repeated-dispatch idempotency;
- preserved input/output Artifact references;
- preserved acceptance and process-instance Evidence references;
- no credential or broker-address material in the report.

`issue388-restart.json` is the compact sanitized artifact suitable for the #388 issue record. Keep
the first and second reports as supporting evidence.

## TLS + HMAC alternative

The same harness may use TLS plus HMAC instead of mTLS client certificates. In that case:

1. configure `PLATFORM_TRANSPORT_AUTH_KEY` in the broker process from an operator-managed secret;
2. configure the same environment variable independently on Host A and Host B;
3. keep broker server TLS verification (`--ca-file` / `--server-hostname`);
4. omit client `--cert-file` / `--key-file` from the harness commands;
5. do not place the HMAC value on argv, in profiles, logs or reports.

The harness rejects a remote acceptance connection that has neither a client certificate nor a
runtime HMAC credential.

## Recording the acceptance on #388

The PR that introduces this harness does **not** by itself satisfy the remaining #388 criterion.
The issue can close only after the commands above are actually run on two independent hosts.

Record a sanitized issue comment containing:

- exact tested Git commit SHA;
- statement that Host A and Host B were independent hosts;
- transport authentication mode (`mtls`, `tls+hmac` or `mtls+hmac`);
- `worker_id` from the sanitized report;
- pass result from `issue388-restart.json`;
- confirmation that first/second reports contain both expected Artifact references and Evidence
  references;
- the required repository validation/check status for the exact tested/merged change.

Attach or quote only sanitized JSON evidence. Do not include broker addresses, certificate paths that
reveal sensitive infrastructure layout, private keys, HMAC values or reusable credentials.

## Expected boundary after acceptance

Successful #388 acceptance proves the real network implementation of #35 can carry the canonical
Worker command/result contract across hosts with authenticated encryption and stable Worker
identity across process restart. It does **not** claim the broader #562 tunnel, registration,
heartbeat, scheduler/liveness, firewall or failure-reconciliation acceptance; those remain a
separate deployment/conformance concern.
