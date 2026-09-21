# Python test runtime strategy

This document defines the measured runtime baseline, profiling evidence, regression budgets and CI parallelization rules for the Python test suite.

## Measured baseline

The initial baseline uses six completed successful GitHub Actions runs immediately preceding this
change on 2026-09-18 (`ubuntu-latest`, Python 3.12).

| Measurement | Median | Observed range |
| --- | ---: | ---: |
| fast unit tests | 15 s | 11–17 s |
| serial non-unit pytest suite | 224 s | 167–383 s |
| pytest aggregate | 240 s | 178–397 s |
| complete required `test` job | 311 s | 226–466 s |

The sampled runs are PRs #1242, #1239, #1232, #1229, #1228 and #1227. Their exact workflow run
IDs, head SHAs and step durations are retained in `config/python-test-runtime.json`.

Across these samples the non-unit suite represents roughly 94% of median pytest wall-clock time.
It is therefore the dominant contributor and the first safe parallelization target. The wide
167–383 second range also demonstrates normal hosted-runner/load variance; wall-clock budgets must
detect major regressions without treating that variance as test flakiness.

## Post-sharding evidence

The first retained sharded runs already isolate the dominant cost instead of hiding it in one
serial non-unit number:

| Shard | Run | Wall time | Slowest test |
| --- | ---: | ---: | ---: |
| integration | 35384156371 | 120.435 s | 6.874 s |
| integration | 35384102471 | 122.020 s | 8.290 s |
| integration (runner-slow) | 35384227788 | 277.123 s | 9.680 s |
| contract + architecture + release | 35384227788 | 23.081 s | 2.853 s |
| E2E + performance + regression | 35384227788 | 95.651 s | 7.593 s |

The repeated integration runs have a 122.020-second median, so the typical measured critical path
is about 46% shorter than the pre-sharding 224-second median non-unit lane. A third run completed
all 2505 integration tests successfully but took 277.123 seconds on a slow runner. Its only failure
was the then-too-tight runtime budget. This is evidence of hosted-runner timing variance, not test
flakiness, and the hard regression thresholds must allow it.

The slowest integration case is the shipped distributed operator entrypoint acceptance test. It
starts the real broker, distributed server, worker and CLI and waits for readiness/registration.
The next contributors are the canonical replanning path, repository-wide AST boundary scan and
real MCP SDK transports. In the system/regression shard, the largest costs are reference-host
reproducibility setup, secure-entrypoint E2E and recovery/performance campaigns.

Static inspection also found many intentionally delayed SQLite/offload tests (typically
0.03–0.12 seconds), real process/Pipelock/MCP readiness loops, OpenSSL-backed security setup and
long-lived child-process fixtures whose children are explicitly terminated. Those delays express
concurrency, lifecycle or security semantics; they are not treated as accidental sleeps merely
because they are visible. `asyncio.sleep(0)` scheduling yields are likewise not optimization
targets. This is why #1235 first removes serial CI coupling rather than replacing behavioral tests
with mocks.

## Retained timing evidence

Every CI pytest lane is executed through:

```bash
python scripts/ci/run_pytest_lane.py --lane <lane> -- <pytest arguments>
```

The wrapper preserves normal pytest exit semantics and additionally writes:

- JUnit XML with individual testcase timings;
- JSON with wall time, testcase totals, slowest tests, modules/classes and test directories;
- Markdown with the same human-readable profile;
- the Markdown report into the GitHub Actions job summary.

CI uploads these files as artifacts even when a test lane fails. This makes regressions diagnosable instead of relying on transient console timing output.

## CI shards

The former serial sequence

```text
unit -> complete non-unit suite
```

is replaced by independent jobs:

```text
unit
contract + architecture + release
integration
e2e + performance + regression
```

The non-unit grouping follows stable suite responsibility, not GitHub issue numbers. Tests remain serial inside each shard. This deliberately avoids introducing `pytest-xdist` shared-state semantics before process/port/filesystem-sensitive tests have been proven parallel-safe.

`scripts/ci/verify_pytest_shards.py` compares the canonical serial collection

```bash
pytest --collect-only -q -m "not unit" tests
```

with the exact union of all non-unit shards. CI fails when a test is missing, unexpectedly added to a shard, or duplicated across shards. New canonical-suite tests therefore cannot be silently dropped by the parallel strategy.

The existing required GitHub check named `test` remains an aggregate gate. It succeeds only when static/package validation, every pytest shard and shard-coverage verification succeed.

## Parallel-safety constraints

The current strategy uses process isolation at the CI-job level and intentionally does not run tests concurrently inside one pytest process group.

Keep a test in a serial shard when it depends on any of the following until explicit parallel-safety evidence exists:

- fixed TCP/HTTP ports or process-global server lifecycle;
- environment variables or current-working-directory mutation that escapes fixture restoration;
- process or Worker startup with shared external state;
- fixed filesystem/database paths outside pytest temporary directories;
- OpenSSL or credential material written to shared locations;
- timing-sensitive polling/retry assertions whose semantics change under CPU contention;
- tests that intentionally validate inter-process isolation or cancellation/settlement behavior.

The serial local fallback remains simply:

```bash
pytest
```

No contributor needs xdist or a special runner to execute the authoritative full suite.

## Runtime budgets

Budgets are regression guards, not aspirational performance targets.

The unit wall-clock budget is 30 seconds against an observed 11–17 second baseline. The
contract/architecture/release lane has a 120-second wall budget against a measured 23.081-second
run, with 10-second individual-test and 15-second module thresholds.

Integration uses a 330-second wall budget. The three observed runs are 120.435, 122.020 and
277.123 seconds; 330 seconds is about 19% above the healthy runner-slow maximum. Its 15-second
individual-test and 20-second module thresholds remain above the measured maxima of 9.680 and
17.397 seconds. System/regression uses a 240-second wall budget against a measured 95.651-second
run and the same 15/20-second test/module thresholds.

The complete `python-validation` matrix has a seven-minute job timeout. This deliberately differs
from the typical performance target: the outer fail-safe must leave enough room for the 350-second
validation budget to record timing evidence and fail deterministically instead of being cancelled
before the regression guard can report its result.
The required `test` aggregator downloads all four pytest timing artifacts and fails if their
measured pytest critical path exceeds 330 seconds or if any expected lane report is missing.
Typical performance remains tracked separately from the hard guard; the current integration median
is 122.020 seconds versus the old 224-second median serial non-unit lane.

The serial full-suite fallback has a 480-second reference budget through the `full-local` lane.
That threshold is approximately 21% above the worst 397-second serial pytest observation from the
six pre-sharding reference runs. It is a regression threshold for comparable hardware, not a claim
that every developer machine must have identical absolute timing.

A budget failure is a test failure; it does not silently skip tests. Timing-budget failures are
also distinguishable from pytest failures so healthy but unusually slow runs can be diagnosed
without misclassifying them as behavioral flakes.

## Local commands

Run the authoritative full suite:

```bash
pytest
```

Run the fast lane:

```bash
pytest -m unit tests/unit
```

Run the previous serial non-unit selection:

```bash
pytest -m "not unit"
```

Verify that CI shards still cover exactly that non-unit selection:

```bash
python scripts/ci/verify_pytest_shards.py
```

Generate the same retained runtime report locally:

```bash
python scripts/ci/run_pytest_lane.py \
  --lane integration \
  -- -m "not unit" tests/integration
```

Run the complete serial suite with the checked-in reference budget:

```bash
python scripts/ci/run_pytest_lane.py \
  --lane full-local \
  -- tests
```

The simpler `pytest` command remains authoritative for correctness and does not require the
profiling wrapper.

## Follow-up profiling

The timing artifacts are the input for the next optimization pass. Changes to fixtures, sleeps/polling, subprocess startup, database setup or test consolidation should be made only when the reports show a measurable contributor and correctness coverage remains equivalent.

In particular, adding xdist is not a goal by itself. It should be introduced only for lanes whose state isolation has been demonstrated and where it reduces wall time beyond the already isolated CI shards.
