"""Optional read-only Linux host-pressure provider for issue #500.

Linux procfs/sysfs/cgroup-v2 details remain adapter metadata. The canonical scheduler consumes
only :class:`HostPressureSnapshot` and portable pressure signals from ``pressure.py``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue

from .models import utc_now
from .pressure import HostPressureSnapshot, PressureKind, PressureSignal, PressureState

_LINUX_METADATA_NAMESPACE: Final = "linux.host_pressure"
_KIB: Final = 1024


@dataclass(frozen=True, slots=True)
class RatioThreshold:
    elevated: float
    critical: float

    def __post_init__(self) -> None:
        if not 0 <= self.elevated <= self.critical <= 1:
            raise ValueError("ratio thresholds must satisfy 0 <= elevated <= critical <= 1")


@dataclass(frozen=True, slots=True)
class LowRatioThreshold:
    elevated: float
    critical: float

    def __post_init__(self) -> None:
        if not 0 <= self.critical <= self.elevated <= 1:
            raise ValueError("low-ratio thresholds must satisfy 0 <= critical <= elevated <= 1")


@dataclass(frozen=True, slots=True)
class ValueThreshold:
    elevated: float
    critical: float

    def __post_init__(self) -> None:
        if not 0 <= self.elevated <= self.critical:
            raise ValueError("value thresholds must satisfy 0 <= elevated <= critical")


@dataclass(frozen=True, slots=True)
class LinuxPressureThresholds:
    """Overrideable Linux normalization thresholds; none are machine requirements."""

    cpu_psi_avg10: ValueThreshold = ValueThreshold(20.0, 50.0)
    memory_psi_avg10: ValueThreshold = ValueThreshold(5.0, 20.0)
    io_psi_avg10: ValueThreshold = ValueThreshold(10.0, 30.0)
    swap_usage: RatioThreshold = RatioThreshold(0.75, 0.90)
    paging_events_per_second: ValueThreshold = ValueThreshold(1.0, 100.0)
    major_faults_per_second: ValueThreshold = ValueThreshold(1.0, 50.0)
    storage_free_ratio: LowRatioThreshold = LowRatioThreshold(0.10, 0.05)
    inode_free_ratio: LowRatioThreshold = LowRatioThreshold(0.10, 0.05)
    file_descriptor_usage: RatioThreshold = RatioThreshold(0.80, 0.95)
    pid_usage: RatioThreshold = RatioThreshold(0.80, 0.95)
    throttle_events_per_second: ValueThreshold = ValueThreshold(1.0, 100.0)


@dataclass(frozen=True, slots=True)
class PsiLine:
    avg10: float
    avg60: float
    avg300: float
    total_microseconds: int


@dataclass(frozen=True, slots=True)
class PsiReport:
    some: PsiLine | None = None
    full: PsiLine | None = None


@dataclass(frozen=True, slots=True)
class _CounterSample:
    observed_at: datetime
    swap_events: int | None
    major_faults: int | None
    cgroup_nr_throttled: int | None
    cgroup_oom: int | None
    cgroup_oom_kill: int | None


@dataclass(frozen=True, slots=True)
class _CgroupEvidence:
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    pid_ratio: float | None = None
    nr_throttled: int | None = None
    oom: int | None = None
    oom_kill: int | None = None


@dataclass(frozen=True, slots=True)
class _CounterRates:
    metadata: dict[str, JsonValue]
    paging_events_per_second: float | None = None
    major_faults_per_second: float | None = None
    throttle_events_per_second: float | None = None
    oom_delta: int | None = None
    oom_kill_delta: int | None = None


class LinuxHostPressureProvider:
    """Collect stable Linux pressure evidence without mutating the host.

    Paths are constructor-injected so tests and deployments can point the provider at fixtures or
    mounted proc/sys/cgroup views. Missing files are unsupported evidence, not errors.
    """

    def __init__(
        self,
        *,
        proc_root: str | Path = "/proc",
        sys_root: str | Path = "/sys",
        cgroup_root: str | Path | None = "/sys/fs/cgroup",
        storage_path: str | Path | None = "/",
        thresholds: LinuxPressureThresholds | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.proc_root = Path(proc_root)
        self.sys_root = Path(sys_root)
        self.cgroup_root = None if cgroup_root is None else Path(cgroup_root)
        self.storage_path = None if storage_path is None else Path(storage_path)
        self.thresholds = thresholds or LinuxPressureThresholds()
        self.clock = clock
        self._previous: _CounterSample | None = None

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot:
        if not node_id.strip():
            raise ValueError("node_id must not be blank")
        observed_at = self.clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Linux pressure provider clock must return timezone-aware datetimes")

        metadata: dict[str, JsonValue] = {}
        signals: list[PressureSignal] = []

        for resource, kind, threshold in (
            ("cpu", PressureKind.CPU, self.thresholds.cpu_psi_avg10),
            ("memory", PressureKind.MEMORY, self.thresholds.memory_psi_avg10),
            ("io", PressureKind.IO, self.thresholds.io_psi_avg10),
        ):
            psi = parse_psi(self._read(self.proc_root / "pressure" / resource))
            if psi is None:
                continue
            metadata[f"psi.{resource}"] = _psi_json(psi)
            primary = psi.some or psi.full
            if primary is not None:
                signals.append(
                    PressureSignal(
                        kind=kind,
                        state=_high_state(primary.avg10, threshold),
                        value=primary.avg10,
                        unit="percent_stall_avg10",
                    )
                )

        meminfo = parse_meminfo(self._read(self.proc_root / "meminfo"))
        swap_total = meminfo.get("SwapTotal")
        swap_free = meminfo.get("SwapFree")
        swap_used: int | None = None
        if swap_total is not None and swap_free is not None:
            swap_used = max(0, swap_total - swap_free)
            metadata["swap.total_bytes"] = swap_total
            metadata["swap.used_bytes"] = swap_used
            metadata["swap.free_bytes"] = swap_free

        vmstat = parse_keyed_ints(self._read(self.proc_root / "vmstat"))
        swap_events = _sum_optional(vmstat.get("pswpin"), vmstat.get("pswpout"))
        major_faults = vmstat.get("pgmajfault")

        cgroup = self._collect_cgroup()
        metadata.update(cgroup.metadata)

        current = _CounterSample(
            observed_at=observed_at,
            swap_events=swap_events,
            major_faults=major_faults,
            cgroup_nr_throttled=cgroup.nr_throttled,
            cgroup_oom=cgroup.oom,
            cgroup_oom_kill=cgroup.oom_kill,
        )
        rates = _counter_rates(self._previous, current)
        self._previous = current
        metadata.update(rates.metadata)

        paging_state = PressureState.HEALTHY
        paging_evidence = False
        if swap_total is not None and swap_used is not None and swap_total > 0:
            ratio = swap_used / swap_total
            metadata["swap.utilization_ratio"] = ratio
            paging_state = _max_state(
                paging_state,
                _high_state(ratio, self.thresholds.swap_usage),
            )
            paging_evidence = True
        if rates.paging_events_per_second is not None:
            paging_state = _max_state(
                paging_state,
                _high_state(
                    rates.paging_events_per_second,
                    self.thresholds.paging_events_per_second,
                ),
            )
            paging_evidence = True
        if rates.major_faults_per_second is not None:
            paging_state = _max_state(
                paging_state,
                _high_state(
                    rates.major_faults_per_second,
                    self.thresholds.major_faults_per_second,
                ),
            )
            paging_evidence = True
        if paging_evidence:
            signals.append(PressureSignal(PressureKind.PAGING, paging_state))

        zram = self._collect_zram()
        if zram:
            metadata["zram.devices"] = zram

        storage_signal, storage_metadata, inode_signal = self._collect_storage()
        metadata.update(storage_metadata)
        if storage_signal is not None:
            signals.append(storage_signal)
        if inode_signal is not None:
            signals.append(inode_signal)

        fd_signal, fd_metadata = self._collect_file_descriptors()
        metadata.update(fd_metadata)
        if fd_signal is not None:
            signals.append(fd_signal)

        if cgroup.pid_ratio is not None:
            signals.append(
                PressureSignal(
                    PressureKind.PROCESS,
                    _high_state(cgroup.pid_ratio, self.thresholds.pid_usage),
                    cgroup.pid_ratio,
                    "ratio",
                )
            )
        if rates.throttle_events_per_second is not None:
            signals.append(
                PressureSignal(
                    PressureKind.THROTTLING,
                    _high_state(
                        rates.throttle_events_per_second,
                        self.thresholds.throttle_events_per_second,
                    ),
                    rates.throttle_events_per_second,
                    "events_per_second",
                )
            )
        if rates.oom_delta is not None or rates.oom_kill_delta is not None:
            new_ooms = (rates.oom_delta or 0) + (rates.oom_kill_delta or 0)
            signals.append(
                PressureSignal(
                    PressureKind.EXHAUSTION,
                    PressureState.CRITICAL if new_ooms > 0 else PressureState.HEALTHY,
                    float(new_ooms),
                    "events",
                )
            )

        metadata["collector.read_only"] = True
        metadata["collector.platform"] = "linux"
        return HostPressureSnapshot(
            state=_overall_state(signals),
            observed_at=observed_at,
            signals=tuple(signals),
            source_ref="linux:procfs-sysfs-cgroupv2",
            trusted=True,
            provider_metadata=(AdapterMetadata(_LINUX_METADATA_NAMESPACE, metadata),),
        )

    def _collect_zram(self) -> list[JsonValue]:
        devices: list[JsonValue] = []
        block_root = self.sys_root / "block"
        if not block_root.exists():
            return devices
        swap_priorities = parse_proc_swaps(self._read(self.proc_root / "swaps"))
        for path in sorted(block_root.glob("zram*")):
            mm = parse_int_fields(self._read(path / "mm_stat"))
            disksize = _parse_int(self._read(path / "disksize"))
            if not mm and disksize is None:
                continue
            original = mm[0] if len(mm) > 0 else None
            compressed = mm[1] if len(mm) > 1 else None
            memory_used = mm[2] if len(mm) > 2 else None
            item: dict[str, JsonValue] = {
                "device": path.name,
                "disksize_bytes": disksize,
                "original_data_bytes": original,
                "compressed_data_bytes": compressed,
                "memory_used_bytes": memory_used,
            }
            if original is not None and compressed is not None and compressed > 0:
                item["compression_ratio"] = original / compressed
            if disksize is not None and disksize > 0 and original is not None:
                item["utilization_ratio"] = min(1.0, original / disksize)
            priority = swap_priorities.get(path.name)
            if priority is not None:
                item["swap_priority"] = priority
            devices.append(item)
        return devices

    def _collect_storage(
        self,
    ) -> tuple[PressureSignal | None, dict[str, JsonValue], PressureSignal | None]:
        if self.storage_path is None:
            return None, {}, None
        try:
            stats = os.statvfs(self.storage_path)
        except OSError:
            return None, {}, None
        total_bytes = stats.f_blocks * stats.f_frsize
        free_bytes = stats.f_bavail * stats.f_frsize
        total_inodes = stats.f_files
        free_inodes = stats.f_favail
        metadata: dict[str, JsonValue] = {
            "storage.total_bytes": total_bytes,
            "storage.free_bytes": free_bytes,
        }
        storage_signal: PressureSignal | None = None
        if total_bytes > 0:
            free_ratio = free_bytes / total_bytes
            metadata["storage.free_ratio"] = free_ratio
            storage_signal = PressureSignal(
                PressureKind.STORAGE,
                _low_state(free_ratio, self.thresholds.storage_free_ratio),
                free_ratio,
                "free_ratio",
            )
        inode_signal: PressureSignal | None = None
        if total_inodes > 0:
            inode_ratio = free_inodes / total_inodes
            metadata["inode.total"] = total_inodes
            metadata["inode.free"] = free_inodes
            metadata["inode.free_ratio"] = inode_ratio
            inode_signal = PressureSignal(
                PressureKind.INODE,
                _low_state(inode_ratio, self.thresholds.inode_free_ratio),
                inode_ratio,
                "free_ratio",
            )
        return storage_signal, metadata, inode_signal

    def _collect_file_descriptors(
        self,
    ) -> tuple[PressureSignal | None, dict[str, JsonValue]]:
        file_nr = parse_int_fields(self._read(self.proc_root / "sys" / "fs" / "file-nr"))
        file_max = _parse_int(self._read(self.proc_root / "sys" / "fs" / "file-max"))
        if not file_nr or file_max is None or file_max <= 0:
            return None, {}
        allocated = file_nr[0]
        ratio = allocated / file_max
        return (
            PressureSignal(
                PressureKind.FILE_DESCRIPTOR,
                _high_state(ratio, self.thresholds.file_descriptor_usage),
                ratio,
                "ratio",
            ),
            {
                "file_descriptors.allocated": allocated,
                "file_descriptors.maximum": file_max,
                "file_descriptors.utilization_ratio": ratio,
            },
        )

    def _collect_cgroup(self) -> _CgroupEvidence:
        root = self.cgroup_root
        if root is None or not (root / "cgroup.controllers").exists():
            return _CgroupEvidence()
        current = _parse_int(self._read(root / "memory.current"))
        high = _parse_limit(self._read(root / "memory.high"))
        maximum = _parse_limit(self._read(root / "memory.max"))
        events = parse_keyed_ints(self._read(root / "memory.events"))
        pids_current = _parse_int(self._read(root / "pids.current"))
        pids_max = _parse_limit(self._read(root / "pids.max"))
        cpu_stat = parse_keyed_ints(self._read(root / "cpu.stat"))
        metadata: dict[str, JsonValue] = {
            "cgroup.v2": True,
            "cgroup.memory.current_bytes": current,
            "cgroup.memory.high_bytes": high,
            "cgroup.memory.max_bytes": maximum,
            "cgroup.memory.events.high": events.get("high"),
            "cgroup.memory.events.oom": events.get("oom"),
            "cgroup.memory.events.oom_kill": events.get("oom_kill"),
            "cgroup.pids.current": pids_current,
            "cgroup.pids.max": pids_max,
            "cgroup.cpu.nr_throttled": cpu_stat.get("nr_throttled"),
            "cgroup.cpu.throttled_usec": cpu_stat.get("throttled_usec"),
        }
        pid_ratio = (
            None
            if pids_current is None or pids_max is None or pids_max <= 0
            else pids_current / pids_max
        )
        return _CgroupEvidence(
            metadata=metadata,
            pid_ratio=pid_ratio,
            nr_throttled=cpu_stat.get("nr_throttled"),
            oom=events.get("oom"),
            oom_kill=events.get("oom_kill"),
        )

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None


def parse_psi(text: str | None) -> PsiReport | None:
    if text is None:
        return None
    values: dict[str, PsiLine] = {}
    for raw_line in text.splitlines():
        parts = raw_line.split()
        if not parts or parts[0] not in {"some", "full"}:
            continue
        fields: dict[str, str] = {}
        for item in parts[1:]:
            key, separator, value = item.partition("=")
            if separator:
                fields[key] = value
        try:
            values[parts[0]] = PsiLine(
                avg10=float(fields["avg10"]),
                avg60=float(fields["avg60"]),
                avg300=float(fields["avg300"]),
                total_microseconds=int(fields["total"]),
            )
        except (KeyError, ValueError):
            continue
    if not values:
        return None
    return PsiReport(some=values.get("some"), full=values.get("full"))


def parse_meminfo(text: str | None) -> dict[str, int]:
    values: dict[str, int] = {}
    if text is None:
        return values
    for raw_line in text.splitlines():
        key, separator, remainder = raw_line.partition(":")
        if not separator:
            continue
        parts = remainder.strip().split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1].lower() == "kb":
            value *= _KIB
        values[key] = value
    return values


def parse_keyed_ints(text: str | None) -> dict[str, int]:
    values: dict[str, int] = {}
    if text is None:
        return values
    for raw_line in text.splitlines():
        parts = raw_line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return values


def parse_int_fields(text: str | None) -> tuple[int, ...]:
    if text is None:
        return ()
    try:
        return tuple(int(item) for item in text.split())
    except ValueError:
        return ()


def parse_proc_swaps(text: str | None) -> dict[str, int]:
    priorities: dict[str, int] = {}
    if text is None:
        return priorities
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            priority = int(parts[4])
        except ValueError:
            continue
        priorities[Path(parts[0]).name] = priority
    return priorities


def _counter_rates(previous: _CounterSample | None, current: _CounterSample) -> _CounterRates:
    if previous is None:
        return _CounterRates(metadata={"counter_delta.available": False})
    elapsed = (current.observed_at - previous.observed_at).total_seconds()
    if elapsed <= 0:
        return _CounterRates(metadata={"counter_delta.available": False})

    paging_delta = _delta(previous.swap_events, current.swap_events)
    major_delta = _delta(previous.major_faults, current.major_faults)
    throttle_delta = _delta(previous.cgroup_nr_throttled, current.cgroup_nr_throttled)
    oom_delta = _delta(previous.cgroup_oom, current.cgroup_oom)
    oom_kill_delta = _delta(previous.cgroup_oom_kill, current.cgroup_oom_kill)
    metadata: dict[str, JsonValue] = {
        "counter_delta.available": True,
        "counter_delta.interval_seconds": elapsed,
        "paging.swap_events_delta": paging_delta,
        "paging.major_faults_delta": major_delta,
        "cgroup.cpu.nr_throttled_delta": throttle_delta,
        "cgroup.memory.oom_delta": oom_delta,
        "cgroup.memory.oom_kill_delta": oom_kill_delta,
    }
    return _CounterRates(
        metadata=metadata,
        paging_events_per_second=None if paging_delta is None else paging_delta / elapsed,
        major_faults_per_second=None if major_delta is None else major_delta / elapsed,
        throttle_events_per_second=None if throttle_delta is None else throttle_delta / elapsed,
        oom_delta=oom_delta,
        oom_kill_delta=oom_kill_delta,
    )


def _delta(previous: int | None, current: int | None) -> int | None:
    if previous is None or current is None:
        return None
    return max(0, current - previous)


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def _parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _parse_limit(text: str | None) -> int | None:
    if text is None or text.strip() == "max":
        return None
    return _parse_int(text)


def _psi_json(report: PsiReport) -> JsonValue:
    def line(value: PsiLine | None) -> JsonValue:
        if value is None:
            return None
        return {
            "avg10": value.avg10,
            "avg60": value.avg60,
            "avg300": value.avg300,
            "total_microseconds": value.total_microseconds,
        }

    return {"some": line(report.some), "full": line(report.full)}


def _high_state(value: float, threshold: RatioThreshold | ValueThreshold) -> PressureState:
    if value >= threshold.critical:
        return PressureState.CRITICAL
    if value >= threshold.elevated:
        return PressureState.ELEVATED
    return PressureState.HEALTHY


def _low_state(value: float, threshold: LowRatioThreshold) -> PressureState:
    if value <= threshold.critical:
        return PressureState.CRITICAL
    if value <= threshold.elevated:
        return PressureState.ELEVATED
    return PressureState.HEALTHY


def _max_state(left: PressureState, right: PressureState) -> PressureState:
    rank = {
        PressureState.UNKNOWN: 0,
        PressureState.HEALTHY: 1,
        PressureState.ELEVATED: 2,
        PressureState.CRITICAL: 3,
    }
    return left if rank[left] >= rank[right] else right


def _overall_state(signals: list[PressureSignal]) -> PressureState:
    if not signals:
        return PressureState.UNKNOWN
    state = PressureState.HEALTHY
    for signal in signals:
        state = _max_state(state, signal.state)
    return state


__all__ = [
    "LinuxHostPressureProvider",
    "LinuxPressureThresholds",
    "LowRatioThreshold",
    "PsiLine",
    "PsiReport",
    "RatioThreshold",
    "ValueThreshold",
    "parse_int_fields",
    "parse_keyed_ints",
    "parse_meminfo",
    "parse_proc_swaps",
    "parse_psi",
]
