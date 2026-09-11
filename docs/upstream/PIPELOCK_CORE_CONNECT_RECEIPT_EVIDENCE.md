# Pipelock Core strict CONNECT receipt evidence

This document records the focused follow-up evidence for issue #730 that isolates the previously
observed CONNECT receipt error from the evaluation workflow's teardown behavior.

## Evaluated boundary

- **Upstream repository:** `luckyPipewrench/pipelock`
- **Pinned upstream revision:** `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- **Build path:** reviewed tag-free Core `make build`
- **Candidate workflow run:** `34590657602` (run #43)
- **Evidence artifact digest:**
  `sha256:6f7d21dd3016519072733e51ebe84116e1575840c6c65127ef8b34a1a9f37f8b`
- **Run-specific candidate binary SHA-256:**
  `9d2c3798922b54b84972d8ea3a44a81280d4d56c11822d9d997a645d8db0eb50`
- **Generated audit-config SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`

As with the other #730 runs, the binary hash is a run fingerprint and is not a byte-for-byte
reproducible-build claim.

## Why this follow-up was required

The earlier forward/CONNECT compatibility probe repeatedly logged:

`chain sealed: transcript root already emitted`

Inspection of the retained runtime log showed the ordering that produced the error:

1. the CONNECT tunnel opened and the client completed its request;
2. the shell step exited and its `EXIT` trap sent process termination;
3. Pipelock logged `shutdown` and sealed the recorder transcript root;
4. the CONNECT relay then attempted its close-time allow receipt;
5. that receipt failed because the writer had already been sealed;
6. `tunnel_close` was logged afterward.

That ordering meant the prior observation could not distinguish a CONNECT receipt defect from a test
harness teardown race.

## Isolated strict-CONNECT experiment

The focused integration test
`test_connect_require_receipts_uses_fresh_writer_and_verifies_chain` removes that race from the
experiment:

1. it reuses the exact generated and validated audit configuration from the candidate workflow;
2. it creates a controlled copy that changes only the settings needed for this probe:
   `forward_proxy.enabled: true`, `flight_recorder.require_receipts: true`, and an isolated recorder
   directory;
3. it uses a fresh HOME/signing context and derives the corresponding public verification key;
4. it starts a dedicated Pipelock process on a random loopback port;
5. it performs one real HTTPS request to `https://example.com/` through the HTTP proxy, exercising
   CONNECT;
6. it waits until Pipelock has logged `tunnel_close` before process termination;
7. it requires that neither `receipt_emission_failed` nor
   `chain sealed: transcript root already emitted` appears;
8. after shutdown it verifies the complete isolated writer chain with `pipelock verify-receipt` and
   the derived public key.

The existing candidate workflow was reused; no additional workflow, paid service or platform runtime
dependency was added.

## Retained strict-CONNECT result

Run `34590657602` records the strict CONNECT result directly as JUnit properties in
`pipelock-adversarial-junit.xml`:

- tests in the adversarial/security suite: **5**;
- errors: **0**;
- failures: **0**;
- skipped: **0**;
- total suite time: **1.318 s**;
- strict CONNECT testcase time: **0.321 s**;
- `strict_connect_chain_valid`: **true**;
- `strict_connect_receipts`: **5**;
- `strict_connect_final_seq`: **4**;
- `strict_connect_root_hash`:
  `03ba36e77f5d33b2ce9e97cae1f70e5624b28fc0b3de9b57513db7d669c8d929`;
- `strict_connect_signer`:
  `3266d99ae62a71933587d583cae3a4611d71597e8c35cde3444abf76300eb4f1`;
- `strict_connect_sealed_chain_error`: **false**;
- `strict_connect_containment`: **UNKNOWN**.

The successful testcase necessarily includes the complete `verify-receipt` invocation after the
isolated Pipelock process stopped. A green testcase therefore means the full fresh-writer chain was
accepted with the derived public key, not merely that the CONNECT transport succeeded.

## Classification of the earlier observation

The evidence now supports classifying the repeated best-effort error from the older workflow probe as
an **evaluation-harness teardown race**:

- the old probe terminates the Pipelock process immediately after curl returns and can seal the
  recorder before the relay emits its close-time receipt;
- the isolated strict probe waits for `tunnel_close` first and then verifies a valid signed chain;
- the strict probe runs with `require_receipts: true` and shows no sealed-chain error.

This does **not** prove that every possible CONNECT lifecycle or abnormal shutdown path is receipt
complete. It does show that the normal tested CONNECT lifecycle can produce and verify strict signed
receipt evidence when the harness does not tear the recorder down before tunnel completion.

## Remaining boundary

The verifier still reports containment as **UNKNOWN**. Receipt-chain validity therefore proves the
integrity/provenance of the mediated writer stream; it does not prove that an agent or child process
could not bypass Pipelock through a direct socket or another unmediated network path.

The #730 evaluation must still keep containment/bypass evidence, broader adversarial network cases,
outage/recovery behavior, representative VPS resource/performance measurements and the final adoption
classification separate from this CONNECT receipt result.
