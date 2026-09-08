# Decision Records

Issue #598 adds a canonical, lightweight record of **why** the platform chose an option. A Decision Record is evidence-backed governance history, not an execution or permission primitive.

## Boundary

A Decision Record is distinct from:

- ADRs: architecture-significant decisions may still have an ADR; the record can reference it;
- Approvals: an Approval authorizes one exact action, while a Decision Record captures rationale;
- Verification and Evaluation: these produce evidence/findings; the Decision Record interprets them;
- Research: Research Items are reusable source material and can be referenced exactly;
- owner-domain mutations: model/plugin/provider/policy activation remains owned by that domain.

The intended flow is:

`Research / Evaluation / Verification -> Decision Record -> optional Approval -> owner-domain action`

The Decision subsystem has no API that activates a plugin, provider, model, policy, Worker or external side effect. `DecisionService.action_provenance()` only returns the record ID/digest/outcome for the actual owner-domain operation to persist after its own authorization/approval checks.

## Immutability and supersession

`DecisionRecord` payload rows are append-only. Their digest is calculated from the reviewable content and never rewritten. Supersession creates a new Decision Record with `supersedes=<old id>` and stores the reverse `superseded_by` relation separately. Withdrawal and downstream provenance are also append-only lifecycle relations. The read projection derives `current`, `superseded` or `withdrawn` without changing the historical record.

A superseding record must preserve the same decision scope, subject and exact subject-resource identity. Superseded or withdrawn records cannot drive new downstream provenance.

## Evidence references

`DecisionReference` supports a resource kind/ID plus optional revision, digest, locator and safe metadata. This allows exact links to Research Items, Evaluation Runs/Comparisons, Verification/Security/Performance findings, cost/resource evidence and ADRs without making any of those domains subordinate to Decision Records.

Deployments can register per-kind reference resolvers through `DecisionReferenceValidator`; unresolved kinds remain structurally valid so progressive dependencies do not become hard runtime imports.

## Control Plane, Search, CLI and Web

Register:

- `decision_record_resource_services(decisions)` for `decision-records`;
- `decision_record_command_handlers(decisions)` for create, supersede, withdraw and append-only downstream provenance linking.

The generic versioned Control Plane then exposes list/detail resources and commands. Registered resources automatically participate in the platform-wide Search rebuild/index seam, so no Decision-specific search backend is introduced.

The existing generic CLI supports:

```bash
platform extension list decision-records
platform extension show decision-records decision_record_...
```

List/search/filter behavior uses the normal Control Plane query contract (`q`, filters, sort, fields, pagination). Web clients can use the same fixed `decision-records` collection through `ControlPlaneCollectionClient`; no provider-private endpoint is required.

For scoped deployments, pass an object-scoped `visibility` callback to `decision_record_resource_services(...)`. Hidden records return `not found` on direct reads so unauthorized callers do not learn that a private decision exists. The normal Control Plane authorization boundary still gates collection reads and every mutation command.

## Portability

`export_decision_bundle()` and `import_decision_bundle()` preserve immutable decision content, evidence references, supersession history, withdrawal state and downstream provenance. The bundle explicitly declares `activation_semantics: none`; import code has no callback or owner-domain activation interface.

Imported history therefore cannot enable providers/plugins, grant permissions or execute external effects merely because the original decision had an `adopt` outcome.

## Review/revisit

Records can carry `review_at` and/or `review_condition`. The northbound projection exposes `revisit_due` when the timestamp is due. Re-evaluation creates a new Decision Record rather than editing historical rationale.
