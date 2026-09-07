from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.distributed.linux_pressure import (
    LinuxHostPressureProvider,
    PsiLine,
    ValueThreshold,
    parse_meminfo,
    parse_psi,
)
from ai_multi_agent_platform.distributed.pressure import PressureKind, PressureState
from ai_multi_agent_platform.domain import new_id

NOW = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _linux_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    proc = tmp_path / "proc"
    sys = tmp_path / "sys"
    cgroup = tmp_path / "cgroup"

    _write(
        proc / "pressure" / "cpu",
        "some avg10=1.00 avg60=2.00 avg300=3.00 total=1000\n",
    )
    _write(
        proc / "pressure" / "memory",
        "some avg10=25.00 avg60=10.00 avg300=5.00 total=2000\n"
        "full avg10=2.00 avg60=1.00 avg300=0.50 total=500\n",
    )
    _write(
        proc / "pressure" / "io",
        "some avg10=12.00 avg60=5.00 avg300=2.00 total=3000\n",
    )
    _write(
        proc / "meminfo",
        "MemTotal:       16000000 kB\nSwapTotal:       1000000 kB\nSwapFree:         200000 kB\n",
    )
    _write(proc / "vmstat", "pswpin 100\npswpout 200\npgmajfault 50\n")
    _write(proc / "sys" / "fs" / "file-nr", "900 0 1000\n")
    _write(proc / "sys" / "fs" / "file-max", "1000\n")
    _write(
        proc / "swaps",
        "Filename Type Size Used Priority\n/dev/zram0 partition 1000000 100000 -2\n",
    )

    _write(sys / "block" / "zram0" / "disksize", "1048576000\n")
    _write(
        sys / "block" / "zram0" / "mm_stat",
        "524288000 209715200 230686720 0 0 0 0 0 0\n",
    )

    _write(cgroup / "cgroup.controllers", "cpu memory pids\n")
    _write(cgroup / "memory.current", "500000000\n")
    _write(cgroup / "memory.high", "800000000\n")
    _write(cgroup / "memory.max", "1000000000\n")
    _write(cgroup / "memory.events", "low 0\nhigh 3\nmax 0\noom 0\noom_kill 0\n")
    _write(cgroup / "pids.current", "90\n")
    _write(cgroup / "pids.max", "100\n")
    _write(
        cgroup / "cpu.stat",
        "usage_usec 100000\nuser_usec 50000\nsystem_usec 50000\nnr_periods 100\n"
        "nr_throttled 2\nthrottled_usec 1000\n",
    )
    return proc, sys, cgroup


def test_parse_psi_preserves_avg_windows_and_total() -> None:
    report = parse_psi(
        "some avg10=1.25 avg60=2.50 avg300=3.75 total=12345\n"
        "full avg10=0.25 avg60=0.50 avg300=0.75 total=2345\n"
    )

    assert report is not None
    assert report.some == PsiLine(1.25, 2.5, 3.75, 12345)
    assert report.full == PsiLine(0.25, 0.5, 0.75, 2345)


def test_parse_meminfo_converts_kib_without_treating_swap_as_ram() -> None:
    values = parse_meminfo("MemTotal: 100 kB\nSwapTotal: 50 kB\nSwapFree: 20 kB\n")

    assert values["MemTotal"] == 100 * 1024
    assert values["SwapTotal"] == 50 * 1024
    assert values["SwapFree"] == 20 * 1024


def test_linux_provider_collects_psi_swap_zram_cgroup_pid_and_descriptor_evidence(
    tmp_path: Path,
) -> None:
    proc, sys, cgroup = _linux_fixture(tmp_path)
    provider = LinuxHostPressureProvider(
        proc_root=proc,
        sys_root=sys,
        cgroup_root=cgroup,
        storage_path=None,
        clock=lambda: NOW,
    )

    snapshot = provider.snapshot_for_node(new_id("node"))
    metadata = snapshot.provider_metadata[0].values

    assert snapshot.state is PressureState.CRITICAL
    assert snapshot.signal(PressureKind.CPU).state is PressureState.HEALTHY  # type: ignore[union-attr]
    assert snapshot.signal(PressureKind.MEMORY).state is PressureState.CRITICAL  # type: ignore[union-attr]
    assert snapshot.signal(PressureKind.IO).state is PressureState.ELEVATED  # type: ignore[union-attr]
    assert snapshot.signal(PressureKind.PAGING).state is PressureState.ELEVATED  # type: ignore[union-attr]
    assert snapshot.signal(PressureKind.PROCESS).state is PressureState.ELEVATED  # type: ignore[union-attr]
    assert snapshot.signal(PressureKind.FILE_DESCRIPTOR).state is PressureState.ELEVATED  # type: ignore[union-attr]
    assert metadata["swap.total_bytes"] == 1_000_000 * 1024
    assert metadata["swap.used_bytes"] == 800_000 * 1024
    assert metadata["cgroup.memory.high_bytes"] == 800_000_000
    assert metadata["cgroup.memory.events.high"] == 3
    zram = metadata["zram.devices"]
    assert isinstance(zram, list)
    assert zram[0]["device"] == "zram0"  # type: ignore[index]
    assert zram[0]["swap_priority"] == -2  # type: ignore[index]
    assert zram[0]["compression_ratio"] == pytest.approx(2.5)  # type: ignore[index]


def test_linux_provider_uses_counter_deltas_for_paging_throttling_and_new_ooms(
    tmp_path: Path,
) -> None:
    proc, sys, cgroup = _linux_fixture(tmp_path)
    times = iter((NOW, NOW + timedelta(seconds=10)))
    provider = LinuxHostPressureProvider(
        proc_root=proc,
        sys_root=sys,
        cgroup_root=cgroup,
        storage_path=None,
        clock=lambda: next(times),
    )

    first = provider.snapshot_for_node(new_id("node"))
    assert first.signal(PressureKind.EXHAUSTION) is None

    _write(proc / "vmstat", "pswpin 700\npswpout 700\npgmajfault 550\n")
    _write(cgroup / "memory.events", "low 0\nhigh 5\nmax 0\noom 1\noom_kill 1\n")
    _write(
        cgroup / "cpu.stat",
        "usage_usec 200000\nuser_usec 100000\nsystem_usec 100000\nnr_periods 200\n"
        "nr_throttled 22\nthrottled_usec 5000\n",
    )

    second = provider.snapshot_for_node(new_id("node"))
    metadata = second.provider_metadata[0].values

    assert metadata["paging.swap_events_delta"] == 1_100
    assert metadata["paging.major_faults_delta"] == 500
    assert metadata["cgroup.cpu.nr_throttled_delta"] == 20
    assert second.signal(PressureKind.PAGING).state is PressureState.CRITICAL  # type: ignore[union-attr]
    assert second.signal(PressureKind.THROTTLING).state is PressureState.ELEVATED  # type: ignore[union-attr]
    exhaustion = second.signal(PressureKind.EXHAUSTION)
    assert exhaustion is not None
    assert exhaustion.state is PressureState.CRITICAL
    assert exhaustion.value == 2.0


def test_linux_provider_returns_explicit_unknown_when_fixture_exposes_no_supported_metrics(
    tmp_path: Path,
) -> None:
    provider = LinuxHostPressureProvider(
        proc_root=tmp_path / "missing-proc",
        sys_root=tmp_path / "missing-sys",
        cgroup_root=tmp_path / "missing-cgroup",
        storage_path=None,
        clock=lambda: NOW,
    )

    snapshot = provider.snapshot_for_node(new_id("node"))

    assert snapshot.state is PressureState.UNKNOWN
    assert snapshot.signals == ()
    assert snapshot.provider_metadata[0].values["collector.read_only"] is True


def test_thresholds_are_overrideable_for_provider_specific_normalization() -> None:
    threshold = ValueThreshold(elevated=2.0, critical=5.0)

    assert threshold.elevated == 2.0
    assert threshold.critical == 5.0
    with pytest.raises(ValueError):
        ValueThreshold(elevated=5.0, critical=2.0)
