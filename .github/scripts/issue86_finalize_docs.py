from pathlib import Path

path = Path("docs/VERIFICATION.md")
text = path.read_text()
old = '''- focused regression tests for core, kernel-gate, persistence/recovery, Control Plane authorization, observability, reviewer-Agent and repair semantics.

Remaining issue work is intentionally layered on top of these authorities:

- frontend pending-review/detail/history/action surface;
- broader replacement conformance tests proving equivalent completion semantics across replaceable orchestrators/models/providers.'''
new = '''- dedicated frontend pending-review queue, detail/history surface and authorized human review actions;
- read-only Verification policy/status/history projections directly on Task, Run and Result detail surfaces;
- replacement-conformance coverage across replaceable orchestrators, reviewer models/providers and external Verification providers;
- focused regression tests for core, kernel-gate, persistence/recovery, Control Plane authorization, observability, reviewer-Agent, repair and replacement semantics.

The implementation work owned directly by #86 is complete. Follow-up integrations remain intentionally owned by their respective issues, including #19 evaluation consumption, #75 review notifications, #82 repository diff/test evidence and #78 reusable policy templates. Those integrations may consume canonical Verification facts but do not own Verification or Task completion semantics.'''
if old not in text:
    raise SystemExit("verification documentation completion anchor not found")
path.write_text(text.replace(old, new, 1))
