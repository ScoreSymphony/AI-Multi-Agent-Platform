# CLI data-provider inventory

Issue: #38  
Owning domain: #13

The File, Memory and Knowledge boundaries remain separate replaceable provider contracts. For administrative diagnostics the Control Plane can register a single read-only `data-providers` collection containing only public provider health and capability metadata.

## Inspect providers

```bash
platform extension list data-providers
platform extension show data-providers PROVIDER_ID
```

Each resource identifies the provider role (`file`, `memory` or `knowledge`) and exposes the public provider ID/type, contract version, supported operations, public capabilities/features, health, availability, limits and resource metadata.

Backend-private `adapter_metadata` is deliberately omitted, including from nested capability descriptors.

## Boundary

The path is:

`CLI -> /api/v1/openapi.json -> /api/v1/data-providers -> #13 provider contracts`

The CLI never opens the local file store, memory database, knowledge index or provider implementation directly. If the collection is not registered in the target Control Plane, extension discovery fails and no provider/backend fallback occurs.

This inventory is diagnostic only. It does not add file upload/download, memory reads/writes, knowledge search/reindex or any other content operation. Those operations require appropriately scoped canonical APIs rather than generic administrative access to provider internals.
