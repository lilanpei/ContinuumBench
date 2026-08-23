from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ContactWindow:
    start_s: float
    end_s: float
    bandwidth_mbps: Optional[float] = None


@dataclass(frozen=True)
class ContactTraceWindow:
    start_s: float
    end_s: float
    src: str
    dst: str
    bandwidth_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    contact_open: float = 1.0


class ContactSchedule:
    """Contact-window model for intermittent links."""

    def __init__(
        self,
        period_s: Optional[float] = None,
        on_duration_s: Optional[float] = None,
        up_bandwidth_mbps: float = 50.0,
        down_bandwidth_mbps: float = 0.0,
        windows: Optional[List[ContactWindow]] = None,
        trace_windows: Optional[List[ContactTraceWindow]] = None,
        start_offset_s: float = 0.0,
        symmetric_trace: bool = True,
        repeat_trace: bool = False,
    ):
        """Initialize a periodic or explicit-window contact schedule."""

        self.period_s = float(period_s) if period_s is not None else None
        self.on_duration_s = float(on_duration_s) if on_duration_s is not None else None
        self.up_bandwidth_mbps = float(up_bandwidth_mbps)
        self.down_bandwidth_mbps = float(down_bandwidth_mbps)
        self.windows = sorted(list(windows or []), key=lambda window: window.start_s)
        self.trace_windows = sorted(
            list(trace_windows or []),
            key=lambda window: (window.start_s, window.src, window.dst),
        )
        self.start_offset_s = float(start_offset_s)
        self.symmetric_trace = bool(symmetric_trace)
        self.repeat_trace = bool(repeat_trace)
        self.trace_period_s = self._trace_period_s()

        if self.trace_windows:
            return

        if self.windows:
            return

        if self.period_s is None or self.on_duration_s is None:
            raise ValueError(
                "ContactSchedule needs either explicit windows or periodic period/on_duration"
            )
        if self.period_s <= 0:
            raise ValueError("period_s must be > 0")
        if self.on_duration_s < 0:
            raise ValueError("on_duration_s must be >= 0")

    def link_state(
        self,
        time_s: float,
        src: Optional[str] = None,
        dst: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """Return ``(available, bandwidth_mbps)`` at a given simulation time."""

        trace_window = self._trace_window(time_s, src, dst)
        if trace_window is not None:
            bandwidth = self.up_bandwidth_mbps
            if trace_window.bandwidth_mbps is not None:
                bandwidth = float(trace_window.bandwidth_mbps)
            if trace_window.contact_open <= 0.0:
                return False, self.down_bandwidth_mbps
            return True, bandwidth
        if self.trace_windows:
            return False, self.down_bandwidth_mbps

        t_val = float(time_s)

        for window in self.windows:
            if window.start_s <= t_val < window.end_s:
                bandwidth = self.up_bandwidth_mbps
                if window.bandwidth_mbps is not None:
                    bandwidth = float(window.bandwidth_mbps)
                return True, bandwidth

        if self.windows:
            return False, self.down_bandwidth_mbps

        local_t = (t_val - self.start_offset_s) % self.period_s
        if local_t < self.on_duration_s:
            return True, self.up_bandwidth_mbps
        return False, self.down_bandwidth_mbps

    def edge_updates(
        self,
        time_s: float,
        src: Optional[str] = None,
        dst: Optional[str] = None,
    ) -> Dict[str, float]:
        """Return edge attribute updates for a given time and link."""

        available, bandwidth = self.link_state(time_s, src, dst)
        updates = {
            "contact_open": 1.0 if available else 0.0,
            "contact_bandwidth": float(bandwidth),
        }
        trace_window = self._trace_window(time_s, src, dst)
        if trace_window is not None and trace_window.latency_ms is not None:
            updates["contact_latency"] = float(trace_window.latency_ms)
        return updates

    def _trace_window(
        self,
        time_s: float,
        src: Optional[str],
        dst: Optional[str],
    ) -> Optional[ContactTraceWindow]:
        """Return the active trace window for one directed link."""

        if not self.trace_windows or src is None or dst is None:
            return None

        t_val = self._trace_time_s(time_s)
        src_id = str(src)
        dst_id = str(dst)
        for window in self.trace_windows:
            if not (window.start_s <= t_val < window.end_s):
                continue
            direct = window.src == src_id and window.dst == dst_id
            reverse = self.symmetric_trace and window.src == dst_id and window.dst == src_id
            if direct or reverse:
                return window
        return None

    def _trace_time_s(self, time_s: float) -> float:
        """Map simulation time onto a finite trace when repetition is enabled."""

        t_val = float(time_s)
        if not self.repeat_trace or self.trace_period_s <= 0.0:
            return t_val
        start = self.trace_windows[0].start_s
        return start + ((t_val - start) % self.trace_period_s)

    def _trace_period_s(self) -> float:
        """Return the finite trace duration used for optional repetition."""

        if not self.trace_windows:
            return 0.0
        start = min(window.start_s for window in self.trace_windows)
        end = max(window.end_s for window in self.trace_windows)
        return max(0.0, end - start)

    def trace_edges(self) -> List[Tuple[str, str]]:
        """Return directed edge IDs declared by the contact trace."""

        edges = {(window.src, window.dst) for window in self.trace_windows}
        if self.symmetric_trace:
            edges.update((dst, src) for src, dst in list(edges))
        return sorted(edges)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ContactSchedule":
        """Build a contact schedule from a configuration mapping."""

        mode = str(config.get("mode", "periodic")).lower()
        if mode in {"trace", "trace_file", "mobility_trace", "contact_trace"}:
            trace_path = Path(config["path"])
            if not trace_path.is_absolute():
                trace_path = Path.cwd() / trace_path
            return cls(
                trace_windows=_load_trace_windows(trace_path),
                up_bandwidth_mbps=float(config.get("up_bandwidth_mbps", 50.0)),
                down_bandwidth_mbps=float(
                    config.get("default_bandwidth_mbps", config.get("down_bandwidth_mbps", 0.0))
                ),
                symmetric_trace=bool(config.get("symmetric", True)),
                repeat_trace=bool(config.get("repeat", config.get("repeat_trace", False))),
            )

        if mode in {"windows", "explicit"}:
            raw_windows = config.get("windows", [])
            windows = [
                ContactWindow(
                    start_s=float(window.get("start_s", 0.0)),
                    end_s=float(window.get("end_s", 0.0)),
                    bandwidth_mbps=(
                        None
                        if window.get("bandwidth_mbps") is None
                        else float(window.get("bandwidth_mbps"))
                    ),
                )
                for window in raw_windows
            ]
            return cls(
                windows=windows,
                up_bandwidth_mbps=float(config.get("up_bandwidth_mbps", 50.0)),
                down_bandwidth_mbps=float(config.get("down_bandwidth_mbps", 0.0)),
            )

        return cls(
            period_s=float(config.get("period_s", config.get("period", 20.0))),
            on_duration_s=float(config.get("on_duration_s", config.get("on_s", 5.0))),
            up_bandwidth_mbps=float(config.get("up_bandwidth_mbps", 50.0)),
            down_bandwidth_mbps=float(config.get("down_bandwidth_mbps", 0.0)),
            start_offset_s=float(config.get("start_offset_s", 0.0)),
        )


class ContactScheduleUpdatePolicy:
    """Eclypse edge update-policy adapter for contact schedules."""

    def __init__(
        self,
        *,
        schedule: ContactSchedule,
        contact_edges: Iterable[Tuple[str, str]],
    ) -> None:
        """Create a policy for applying one schedule to selected directed links."""

        self.schedule = schedule
        self.contact_edges = list(contact_edges)
        self.time_s = 0.0
        self.last_updates: Dict[Tuple[str, str], Dict[str, float]] = {}
        self._base_edge_attrs: Dict[Tuple[str, str], Dict[str, float]] = {}

    def set_time(self, time_s: float) -> None:
        """Set the simulation time applied on the next graph evolution."""

        self.time_s = float(time_s)
        self.last_updates = {}

    def apply_edges(self, edges: Any) -> None:
        """Eclypse edge update policy: apply contact state to selected links."""

        updates: Dict[Tuple[str, str], Dict[str, float]] = {}
        for src, dst in self.contact_edges:
            self._apply_one_edge(edges, src, dst, updates)
            self._apply_one_edge(edges, dst, src, updates)
        self.last_updates = updates

    def _apply_one_edge(
        self,
        edges: Any,
        src: str,
        dst: str,
        updates: Dict[Tuple[str, str], Dict[str, float]],
    ) -> None:
        """Apply this schedule to one directed edge when it exists."""

        try:
            attrs = edges[src, dst]
        except Exception:
            return

        edge_key = (src, dst)
        if edge_key not in self._base_edge_attrs:
            self._base_edge_attrs[edge_key] = {
                key: float(attrs[key])
                for key in ("availability", "bandwidth", "latency")
                if key in attrs
            }
        edge_update = self.schedule.edge_updates(self.time_s, src, dst)
        base_attrs = self._base_edge_attrs.get(edge_key, {})
        if "contact_latency" not in edge_update and "latency" in base_attrs:
            edge_update["contact_latency"] = float(base_attrs["latency"])
        attrs.update(edge_update)
        updates[edge_key] = dict(edge_update)


def _load_trace_windows(path: Path) -> List[ContactTraceWindow]:
    """Load contact trace windows from CSV or JSON."""

    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Contact trace file not found: {trace_path}")

    if trace_path.suffix.lower() == ".json":
        rows = json.loads(trace_path.read_text(encoding="utf-8"))
    else:
        with trace_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

    if not isinstance(rows, list):
        raise ValueError("Contact trace must contain a list of window rows")

    windows: List[ContactTraceWindow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Contact trace rows must be objects/mappings")
        windows.append(_trace_window_from_row(row))
    return windows


def _trace_window_from_row(row: Dict[str, Any]) -> ContactTraceWindow:
    """Convert one trace row into a contact trace window."""

    src = row.get("src", row.get("source"))
    dst = row.get("dst", row.get("target"))
    if src is None or dst is None:
        raise ValueError("Contact trace rows require src/source and dst/target")

    start = _required_float(row, "start_s", "start", "start_time_s")
    end = _required_float(row, "end_s", "end", "end_time_s")
    if end <= start:
        raise ValueError("Contact trace row end_s must be greater than start_s")

    return ContactTraceWindow(
        start_s=start,
        end_s=end,
        src=str(src),
        dst=str(dst),
        bandwidth_mbps=_optional_float(row, "bandwidth_mbps", "bandwidth", "bw_mbps"),
        latency_ms=_optional_float(row, "latency_ms", "latency"),
        contact_open=_contact_open_from_row(row),
    )


def _contact_open_from_row(row: Dict[str, Any]) -> float:
    """Read the required runtime contact-open flag from a trace row."""

    if "availability" in row and "contact_open" not in row:
        raise ValueError(
            "Contact trace rows must use contact_open for runtime reachability; "
            "edge availability is not a ContinuumBench contact-trace field."
        )
    value = _optional_float(row, "contact_open", default=1.0)
    return float(value or 0.0)


def _required_float(row: Dict[str, Any], *keys: str) -> float:
    """Read a required float value from one of several aliases."""

    value = _optional_float(row, *keys)
    if value is None:
        raise ValueError(f"Contact trace row missing required value: {keys[0]}")
    return value


def _optional_float(
    row: Dict[str, Any],
    *keys: str,
    default: Optional[float] = None,
) -> Optional[float]:
    """Read an optional float value from one of several aliases."""

    for key in keys:
        value = row.get(key)
        if value in {None, ""}:
            continue
        return float(value)
    return default


__all__ = [
    "ContactSchedule",
    "ContactScheduleUpdatePolicy",
    "ContactTraceWindow",
    "ContactWindow",
]
