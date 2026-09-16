# Frontend API client boundary

`ApiTransport` in `transport.ts` is the only general-purpose HTTP transport for browser calls to the versioned Control Plane.

Domain clients under this directory own endpoint selection, typed request/response models, query encoding and narrowly scoped domain adaptations. They must delegate generic request behavior to `ApiTransport` instead of defining their own JSON parser, generic error mapper, timeout/retry loop, correlation-header construction or direct `fetch` request pipeline.

`BrowserSessionClient` is the approved browser-session boundary. It owns storage and rotation of the non-secret CSRF token and exposes a configured `transport` whose CSRF callback re-reads the current token before unsafe cookie-authenticated requests. The HttpOnly session credential remains inaccessible to frontend code. Its low-level `fetch` member is retained only as a compatibility boundary for non-domain callers; domain clients must accept/share `ApiTransport` directly.

## Transport policy

- Browser requests target the canonical `/api/v1` Control Plane only.
- `Accept`, JSON content headers and correlation IDs are constructed centrally.
- Canonical API error envelopes are preserved; malformed/non-JSON failures are normalized centrally with request/correlation diagnostics.
- Cookie-authenticated unsafe methods receive the current CSRF token; explicit Bearer requests do not.
- Timeouts and caller cancellation are normalized as transport errors.
- GET/HEAD reads may use the bounded transient retry policy. Mutations are never retried automatically merely because they carry an idempotency key.
- A caller that needs different retry semantics must make that policy explicit at the transport boundary; domain clients must not implement ad-hoc retry loops.
- Streaming clients use `requestRaw()` when a successful response body must remain unconsumed. HTTP status/error handling, auth/CSRF, request diagnostics, timeout/cancellation and retry policy still stay inside `ApiTransport`.
- Provider-, Hermes-, Forge-, MCP-, Worker-, database- and storage-private transports are not valid browser fallbacks.

## Generated transport DTOs

Stable v1 wire DTOs are generated from the canonical Control Plane OpenAPI document, not maintained independently in the frontend. The committed output is `generated/control-plane-v1.ts`; do not edit it by hand.

From the repository root, regenerate with:

```text
python scripts/generate_frontend_contracts.py
```

Or from `frontend/`:

```text
npm run generate:contracts
```

`python scripts/generate_frontend_contracts.py --check` and `npm run check:contracts` fail when canonical schemas and the committed TypeScript output drift. `npm test` runs this check before the existing transport-boundary guard and Vitest.

`types.ts` preserves established frontend-facing export names while sourcing the supported Project, Workspace, Task, Run, model, health, manifest, error, Search response, observability timeline and accounting/usage wire shapes from the generated module. Concrete Search, timeline and accounting page envelopes are generated as well.

Search query parameters are also represented canonically in generated OpenAPI as `SearchQueryParameters`. The frontend `SearchRequest` type intentionally remains a small query-builder/view model because plural array fields such as `types`, `statuses`, `tags`, `sources` and `providers` are mapped by `toSearchQuery()` to comma-separated wire query parameters. It must derive scalar enum/value semantics from the generated query contract rather than redeclaring those wire semantics independently.

Generated wire DTOs are not page/view/form state. UI-specific models must remain separate and should map explicitly from generated DTOs at the presentation boundary. The `Page<T>` compatibility helper routes the migrated timeline and accounting element types to their generated concrete page DTOs; its generic fallback remains only for unrelated collection/view surfaces that are outside this migration. `ListQuery`, query-builder state and presentation summaries remain frontend-owned helpers rather than shadow definitions of the migrated resource wire DTOs. Likewise, request and response DTOs are separate where the API applies defaults: optional request fields must not be made required merely because the corresponding canonical response materializes them.

When a supported wire contract changes, update the canonical Control Plane schema first, regenerate the TypeScript file, then update explicit UI/domain mappings as needed. Do not patch the generated file to make the frontend compile. Search, telemetry/observability and accounting/usage changes follow this same rule; they no longer have a manual-wire exception.

## Domain client pattern

Domain clients receive the shared browser-session transport and may retain `ApiTransportOptions` for isolated tests or non-shell composition:

```ts
export interface ExampleClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class ExampleClient {
  private readonly transport: ApiTransport;

  constructor(options: ExampleClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
  }

  get(id: string): Promise<Example> {
    return this.transport.request<Example>(`/examples/${encodeURIComponent(id)}`);
  }
}
```

The shell shares one `BrowserSessionClient.transport` instance across domain clients so session/CSRF, timeout, diagnostics and retry behavior stay coherent.

`npm test` runs the generated-contract drift check and `scripts/check-transport-boundary.mjs` before Vitest. The transport guard has no domain-client exception inventory: any new generic JSON/error/fetch transport owner outside `transport.ts` or the approved browser-session boundary fails the check.
