# Control Plane HA observability

Issue #89 projects active/passive Control Plane state through the existing backend-neutral #16
observability facade. HA does not introduce a dedicated metrics database, tracing product or hosted
monitoring dependency.

## Correctness boundary

Observability is never part of leadership correctness.

- `CoordinationProvider` remains the only authority for leases and fencing epochs.
- Local wall clocks may measure lease age and time since a successful renewal for operator display,
  but may never prove leadership or lease validity.
- Monotonic process time may measure promotion duration only.
- Telemetry exporter failures are best-effort and must not alter promotion, renewal, fencing,
  reconciliation or step-down outcomes.

## Health/status projection

The HA health projection exposes operational metadata sufficient to identify the current process and
its most recent failover state:

- operational Control Plane `instance_id`;
- availability mode and current role;
- current observed leader instance and fencing epoch;
- lease acquisition and expiry timestamps;
- observational lease age;
- last successful renewal timestamp and observational time since that renewal;
- coordination availability;
- promotion count and last promotion reason;
- whether promotion reconciliation is currently in progress;
- last reconciliation counts/details;
- stable `last_error_code` rather than raw exception text.

These fields are operational metadata. They do not become canonical Task, Run, Worker or Node
identity.

## Canonical telemetry component

HA events use `FailureComponent.CONTROL_PLANE_HA`. This keeps coordination/fencing failures distinct
from Worker scheduling, authorization, storage and generic infrastructure failures while remaining
exporter-neutral.

## Metrics

The current HA adapter emits:

| Metric | Meaning |
| --- | --- |
| `platform.control_plane.ha.role_transitions` | operational role changes |
| `platform.control_plane.ha.promotion_attempts` | promotion attempts after coordination inspection |
| `platform.control_plane.ha.promotion_conflicts` | expected attempts blocked by another valid leader |
| `platform.control_plane.ha.promotions` | successful promotions after reconciliation |
| `platform.control_plane.ha.promotion_duration_seconds` | monotonic duration of successful promotion |
| `platform.control_plane.ha.promotion_failures` | failed promotion/reconciliation attempts |
| `platform.control_plane.ha.lease_renewals` | successful active-leader renewals |
| `platform.control_plane.ha.authority_rejections` | rejected authority/fencing checks |
| `platform.control_plane.ha.coordination_failures` | coordination operations that could not prove state |

Attributes are limited to non-secret operational values such as instance ID, mode, role, epoch,
operation, stable failure code, promotion reason and reconciliation counts. No prompt, model input,
secret value, credential or session material is intentionally copied into HA telemetry.

## Timeline and logs

Timeline events describe promotion and role transitions:

- `control_plane.ha.promotion_started`;
- `control_plane.ha.promotion_conflict`;
- `control_plane.ha.promotion_completed`;
- `control_plane.ha.promotion_failed`;
- `control_plane.ha.role_changed`;
- `control_plane.ha.lease_renewed`.

Failure logs use stable classifications for authority rejection and coordination failure. Expected
leadership conflicts are not reported as infrastructure errors because remaining a standby while
another valid leader exists is normal active/passive behavior.

## Operator interpretation

A healthy active instance reports `role=active`, `coordination_available=true`, a non-zero HA epoch
and current leader equal to its own instance ID. A standby may be healthy as a process while not
being write-ready. `promoting` means the process owns a candidate epoch but public mutation,
Automation and new dispatch authority remain disabled until reconciliation completes. `fenced`
means the process must not perform authority-bearing work.

`last_error_code` intentionally survives long enough for diagnosis even when the process is already
fenced. A later successful promotion or successful lease renewal clears it. Operator tooling should
correlate status with the HA timeline/metrics rather than treating local lease-age values as authority
proof.
