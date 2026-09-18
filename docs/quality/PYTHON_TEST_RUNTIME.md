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

The initial 420-second wall budget for each non-unit shard is intentionally conservative: it is
approximately 10% above the worst observed *complete serial non-unit suite*. Because each shard is
a strict subset of that former lane, exceeding this threshold is a strong major-regression signal
without encoding one runner's best-case timing as policy. The threshold should be tightened after
representative sharded timing evidence accumulates.

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

The unit wall-clock budget is 30 seconds against an observed 11–17 second baseline. Each non-unit shard initially has a 420-second wall budget, approximately 10% above the worst observed 383-second complete serial non-unit run. This is deliberately a major-regression guard until representative sharded measurements justify a tighter threshold.

The first per-test/per-module thresholds are intentionally coarse major-regression guards. They are derived from the measured non-unit envelope and must be tightened using retained CI timing artifacts once enough representative runs exist. The checked-in config is the canonical source.

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

## Follow-up profiling

The timing artifacts are the input for the next optimization pass. Changes to fixtures, sleeps/polling, subprocess startup, database setup or test consolidation should be made only when the reports show a measurable contributor and correctness coverage remains equivalent.

In particular, adding xdist is not a goal by itself. It should be introduced only for lanes whose state isolation has been demonstrated and where it reduces wall time beyond the already isolated CI shards.
