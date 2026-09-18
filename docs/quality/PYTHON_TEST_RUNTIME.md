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
| E2E + performance + regression | 35384227788 | 95.651 s | 7.593 s |

The measured pytest critical path is therefore currently the integration shard at about 120–122
seconds. Against the pre-sharding 224-second median non-unit lane, this is about a 46% wall-clock
reduction before counting any further optimization. The repeated integration measurements also
show the same dominant category rather than a one-off outlier.

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
- JSON with wall time, testcase totals, slowest tests and slowest modules/classes;
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
non-unit shard wall budget is 210 seconds: below the old 224-second median serial non-unit lane,
but still about 72% above the repeated 120–122 second integration measurements and more than twice
the measured 95.651-second system/regression shard. Integration and system/regression use a
15-second individual-test and 20-second module/class major-regression threshold; the currently
observed maxima remain below 8.3 seconds.

The complete `python-validation` matrix has a four-minute job timeout. Because its lanes are
independent and run in parallel, that bounds the frontend-independent Python validation critical
path without adding paid runners or unsafe in-process parallelism. The required `test` aggregator
downloads all four pytest timing artifacts and fails if their measured critical path exceeds 210
seconds or if any expected lane report is missing.

The serial full-suite fallback has a 480-second reference budget through the `full-local` lane.
That threshold is approximately 21% above the worst 397-second serial pytest observation from the
six pre-sharding reference runs. It is a regression threshold for comparable hardware, not a claim
that every developer machine must have identical absolute timing.

Contract/architecture/release keeps a deliberately wider 30-second test and 60-second module
threshold until its own retained profile has enough representative evidence. The checked-in config
is the canonical source for every threshold.

A budget failure is a test failure; it does not silently skip tests.

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
