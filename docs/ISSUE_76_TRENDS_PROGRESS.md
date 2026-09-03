# Issue #76 — Time-window trend progress

This follow-up adds bounded historical trend aggregation on top of the durable `UsageRecord` store.

## Semantics

- `AccountingService.trend(...)` requires an explicit metric, unit, start, end and positive bucket width.
- A request is capped at 500 buckets so an API/UI projection cannot accidentally materialize an unbounded history.
- `additive` metrics sum only records inside each bucket.
- `latest` metrics select the latest sample inside each bucket.
- Empty buckets remain `null`/no sample; they are not fabricated as zero.
- `latest` values are not silently carried forward into buckets that have no measurement.
- Measurement-quality counts and unavailable counts remain visible for each bucket.

## Control Plane / UI

The existing `usage-aggregates` resource now includes a default rolling 24-hour history in one-hour buckets. This remains an owner-isolated projection over the same accounting records; it does not create a second usage store.

The `/usage` frontend displays recent non-empty bucket values alongside each aggregate and continues to distinguish `additive` from `latest` semantics.

## Remaining progressive work

Issue #76 remains open. Worker/Node telemetry, authorization/admission enforcement, Agent/Team/Organization attribution, notifications and external/browser/repository measurements remain dependent on their owning domains and must not be fabricated here.
