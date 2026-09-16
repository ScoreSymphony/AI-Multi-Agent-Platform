# Two-host transport evidence compatibility

The maintained two-host MessageTransport implementation, acceptance runner, tests and runtime vocabulary use behavior-oriented names. A small set of `issue388` / `issue-388` values remains intentionally stable because it identifies the versioned evidence contract introduced by issue #388 and already consumed by the real two-VPS acceptance/conformance path.

Retained compatibility/provenance identifiers are limited to:

- `ai-multi-agent-platform/issue-388-two-host-transport/v1`;
- `ai-multi-agent-platform/issue-388-two-host-restart/v1`;
- `evidence:issue388-two-host`;
- retained/sanitized evidence artifact filenames such as `issue388-first.json`, `issue388-second.json` and `issue388-restart.json`.

These values are evidence/schema lineage, not platform architecture, runtime provider identity, test behavior naming, user-facing product vocabulary or ownership. Existing retained evidence and the two-VPS verifier depend on the transport schema identifier, so renaming it would create an unnecessary compatibility break.

New production identifiers, provider/backend IDs, message topics, correlation/idempotency keys, test canaries and maintained comments must use behavior/domain terminology. New issue-number-based evidence identifiers must not be introduced merely by analogy with this historical contract.

Historical context: issue #388 introduced the two-host transport acceptance evidence that these compatibility values identify. Issue #898 removed issue-number semantics from maintained production/test naming while preserving this explicit evidence compatibility boundary.
