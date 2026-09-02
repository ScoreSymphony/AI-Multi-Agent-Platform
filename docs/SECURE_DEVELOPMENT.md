# Secure Development Guidance

This checklist applies to maintainers, contributors and coding agents making security-sensitive changes.

## Before implementation

- Identify the protected asset and trust boundary affected by the change.
- Read `SECURITY.md` and `docs/SECURITY_THREAT_MODEL.md`.
- Confirm whether the work adds a privileged action, external side effect, new credential flow, new executable code path or new untrusted input.
- Check whether an existing canonical identity/policy/approval/workspace boundary already owns the decision.
- Do not create provider-private authorization semantics to make an integration easier.
- For architecture-significant upstream/dependency changes, follow `LICENSE_POLICY.md` and the upstream adoption/update process.

## Canonical authority rules

- Treat model, Agent, retrieved, webpage, tool and connector output as untrusted input.
- Never authorize an action because text claims it is approved, trusted, admin or system-generated.
- Use canonical actor/action/resource context for security-sensitive decisions.
- Backend/provider/plugin/worker identifiers are diagnostic/mapping data, not authority.
- Sensitive operations require backend enforcement; frontend checks may improve UX but are not security boundaries.
- Bind approvals to the concrete canonical operation being reviewed; do not accept generic natural-language approval claims.

## Inputs and serialization

- Validate untrusted data at the nearest canonical boundary.
- Use endpoint/capability schemas for semantic validation and `validate_untrusted_json(...)` where generic structural/resource bounding is useful.
- Reject ambiguous or malformed inputs instead of silently coercing security-relevant fields.
- Add size/depth/count/time limits for inputs that may be attacker-controlled.
- Do not deserialize arbitrary Python objects from untrusted sources.
- Keep adapter-private metadata namespaced and non-authoritative.

## Filesystem and execution

- Resolve untrusted relative paths beneath an explicit workspace root.
- Reject absolute paths and parent traversal where relative paths are required.
- Protect against symlink/junction escapes; use `resolve_within(...)` where applicable.
- Remember path validation alone does not eliminate TOCTOU races; hostile-code execution needs OS/container sandboxing and least privilege.
- Do not add arbitrary shell/command execution to the default path unless a later issue explicitly defines the capability, policy and sandbox model.
- Filter environment variables; never inherit broad host credentials into executor contexts by default.
- Treat produced artifacts as untrusted input when later consumed.

## Secrets and sensitive data

- Prefer scoped `SecretReference` values; do not add plaintext secret fields to normal canonical contracts.
- Resolve secret material only at the narrowest authorized execution boundary.
- Pass log/event/trace/export payloads through reusable redaction before persistence or emission.
- Do not include credentials in exceptions, debug strings, test snapshots or fixture commits.
- When adding a new secret-bearing field or provider, update redaction tests.

## Network, browser and external systems

- Classify operations as read-only, write, destructive or otherwise side-effecting.
- Add authorization/approval before the external side effect, not after it.
- Add timeout, response-size and concurrency limits.
- Keep a policy hook for outbound destinations and SSRF defense.
- Isolate cookies, tokens and sessions by the appropriate user/project/provider scope.
- Verify signatures/nonces/timestamps for webhook-style events when the provider supports them.
- Use idempotency/replay protection for repeated external messages that could repeat side effects.

## Plugins, dependencies and upstreams

- Installation does not equal trust.
- Review requested permissions/capabilities and default them to the minimum required scope.
- Pin or otherwise make dependency/update provenance reproducible where practical.
- Record origin, version/revision, license/provenance and update strategy for architecture-significant upstreams.
- Do not let an upstream redefine canonical platform security or domain contracts without explicit review/ADR.

## Tests required for security-sensitive changes

Add regression coverage for the abuse case being introduced or mitigated. Depending on the subsystem, include:

- malformed input;
- unauthorized/denied operation;
- exact approval binding;
- traversal/symlink/workspace escape;
- secret redaction/leak prevention;
- replay/duplicate delivery;
- cross-project/resource isolation;
- adapter/plugin absence or compromise assumptions;
- worker/service identity mismatch;
- SSRF/destination rejection;
- resource/time/size limit behavior;
- compromised/tampered package or provenance failure.

Use `docs/SECURITY_EXTENSION_CHECKLIST.md` when the change adds or materially changes a trust boundary.

## Review stop conditions

Stop and request architecture/security review when a change would:

- weaken a canonical security invariant;
- bypass backend authorization/approval;
- place plaintext secrets in canonical state or ordinary logs/events/exports;
- allow provider-private paths or IDs to override canonical boundaries;
- make an optional adapter/plugin required for the secure path;
- introduce a privileged capability without policy, audit and regression coverage;
- rely on LLM alignment or prompt wording as the authorization control.
