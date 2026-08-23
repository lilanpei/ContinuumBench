from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Protocol

from continuum_bench.runtime.skeleton import SkeletonService

if TYPE_CHECKING:
    from eclypse.graph import Application, Infrastructure


@dataclass(frozen=True)
class ScaleAction:
    """Scale a worker pool stage to the target active worker count."""

    stage: str
    active_workers: int


@dataclass
class ControllerAction:
    """Joint controller output: scaling updates and an optional placement mapping."""

    scale: List[ScaleAction] = field(default_factory=list)
    placement: Optional[Dict[str, str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerPoolSnapshot:
    """Immutable worker-pool state exposed to controllers."""

    stage: str
    min_workers: int
    max_workers: int
    active_workers: int
    total_workers: int
    active_worker_ids: List[str]
    inactive_worker_ids: List[str]
    ready_workers: int = 0
    ready_worker_ids: List[str] = field(default_factory=list)
    starting_workers: int = 0
    starting_worker_ids: List[str] = field(default_factory=list)


@dataclass
class ControllerState:
    """Read-only runtime state passed to a controller each epoch."""

    epoch: int
    time_s: float
    seed: int
    application: Application
    infrastructure: Infrastructure
    placement: Dict[str, str]
    queue_depths: Dict[str, int]
    oldest_age_s: Dict[str, float]
    ttl_pressure: Dict[str, float]
    buffer_items: Dict[str, int]
    buffer_bytes: Dict[str, int]
    worker_pools: Dict[str, WorkerPoolSnapshot]
    metadata: Dict[str, Any] = field(default_factory=dict)


class Controller(Protocol):
    """Controller protocol used by the benchmark runner."""

    def step(self, state: ControllerState) -> ControllerAction:
        """Return the next controller action for the current state."""
        ...


class WorkerPoolManager:
    """ContinuumBench benchmark replica projection over an Eclypse application.

    Eclypse owns service graphs and placement feasibility, but it does not yet
    expose a controller-facing autoscaling lifecycle. ContinuumBench therefore
    keeps the worker graph stable and projects replica activation into Eclypse
    service attributes before each placement audit. Scaling updates only toggle
    active workers. Inactive workers are marked inactive and their numeric
    requirements are reduced to epsilon so they can be ignored by placement
    feasibility checks.
    """

    def __init__(
        self,
        application: Application,
        worker_pools: Mapping[str, List[str]],
        min_workers: Optional[Mapping[str, int]] = None,
        initial_workers: Optional[Mapping[str, int]] = None,
        idle_epsilon: float = 1e-9,
        startup_delay_s: Optional[Mapping[str, float]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        """Initialize worker pools and activate the initial workers."""
        self.application = application
        self._worker_pools: Dict[str, List[str]] = {
            stage: sorted(list(workers)) for stage, workers in worker_pools.items()
        }
        self._worker_stage: Dict[str, str] = {
            worker_id: stage
            for stage, workers in self._worker_pools.items()
            for worker_id in workers
        }
        self._min_workers: Dict[str, int] = {
            stage: max(0, int((min_workers or {}).get(stage, 0)))
            for stage in self._worker_pools
        }
        self._idle_epsilon = float(idle_epsilon)
        self._startup_delay_s: Dict[str, float] = {
            stage: max(0.0, float((startup_delay_s or {}).get(stage, 0.0)))
            for stage in self._worker_pools
        }
        self._time_fn = time_fn or (lambda: 0.0)

        self._base_node_attrs: Dict[str, Dict[str, Any]] = {
            service_id: dict(application.nodes.get(service_id, {}))
            for service_id in self._worker_stage
        }
        self._active_workers: Dict[str, int] = {stage: 0 for stage in self._worker_pools}
        self._worker_ready_at_s: Dict[str, float] = {}
        self._pending_startup_delay_s: List[float] = []

        for stage, workers in self._worker_pools.items():
            del workers
            default_initial = max(0, self._min_workers.get(stage, 0))
            requested = int((initial_workers or {}).get(stage, default_initial))
            self.set_active_workers(stage, requested)
            ready_now_s = float(self._time_fn())
            for worker_id in self.active_workers(stage):
                self._worker_ready_at_s[worker_id] = ready_now_s

        self._pending_startup_delay_s.clear()
        self._refresh_worker_runtime_states()

    def stages(self) -> List[str]:
        """Return the managed worker-pool stages."""
        return list(self._worker_pools.keys())

    def workers_for_stage(self, stage: str) -> List[str]:
        """Return all worker IDs configured for a stage."""
        return list(self._worker_pools.get(stage, []))

    def active_count(self, stage: str) -> int:
        """Return the active worker count for a stage."""
        return int(self._active_workers.get(stage, 0))

    def startup_delay_seconds(self, stage: str) -> float:
        """Return the configured startup delay for a stage."""
        return float(self._startup_delay_s.get(stage, 0.0))

    def set_active_workers(self, stage: str, k: int) -> List[str]:
        """Clamp and apply the active worker target for a stage."""
        if stage not in self._worker_pools:
            raise KeyError(f"Unknown worker pool stage: {stage}")

        workers = self._worker_pools[stage]
        min_k = self._min_workers.get(stage, 0)
        max_k = len(workers)
        target = max(min_k, min(max_k, int(k)))
        previous_target = self._active_workers.get(stage, 0)

        active_workers = workers[:target]
        active_set = set(active_workers)
        previous_active_set = set(workers[:previous_target])
        activation_time_s = float(self._time_fn())
        startup_delay_s = self.startup_delay_seconds(stage)

        for worker_id in workers:
            if worker_id in active_set and worker_id not in previous_active_set:
                self._worker_ready_at_s[worker_id] = activation_time_s + startup_delay_s
                if startup_delay_s > 0.0:
                    self._pending_startup_delay_s.append(startup_delay_s)
            elif worker_id not in active_set:
                self._worker_ready_at_s.pop(worker_id, None)

        self._active_workers[stage] = target
        self._refresh_worker_runtime_states()
        return active_workers

    def consume_startup_delay_events(self) -> List[float]:
        """Return and clear queued startup delay events."""
        delays = list(self._pending_startup_delay_s)
        self._pending_startup_delay_s.clear()
        return delays

    def active_workers(self, stage: str) -> List[str]:
        """Return the active worker IDs for a stage."""
        workers = self._worker_pools.get(stage, [])
        return workers[: self._active_workers.get(stage, 0)]

    def inactive_workers(self, stage: str) -> List[str]:
        """Return the inactive worker IDs for a stage."""
        workers = self._worker_pools.get(stage, [])
        return workers[self._active_workers.get(stage, 0) :]

    def ready_workers(self, stage: str) -> List[str]:
        """Return active workers whose startup delay has elapsed."""
        self._refresh_worker_runtime_states()
        active_workers = self.active_workers(stage)
        return [worker_id for worker_id in active_workers if self._worker_is_ready(worker_id)]

    def starting_workers(self, stage: str) -> List[str]:
        """Return active workers that are still starting."""
        self._refresh_worker_runtime_states()
        active_workers = self.active_workers(stage)
        return [worker_id for worker_id in active_workers if not self._worker_is_ready(worker_id)]

    def active_service_ids(self) -> List[str]:
        """Return service IDs that are currently active."""
        self._refresh_worker_runtime_states()
        active = []
        worker_ids = set(self._worker_stage)
        active_worker_ids = {
            worker_id
            for stage in self.stages()
            for worker_id in self.active_workers(stage)
        }
        for service_id in self.application.nodes:
            if service_id not in worker_ids:
                active.append(service_id)
                continue
            if service_id in active_worker_ids:
                active.append(service_id)
        return active

    def ready_service_ids(self) -> List[str]:
        """Return service IDs that are currently ready."""
        self._refresh_worker_runtime_states()
        ready = []
        worker_ids = set(self._worker_stage)
        ready_worker_ids = {
            worker_id
            for stage in self.stages()
            for worker_id in self.ready_workers(stage)
        }
        for service_id in self.application.nodes:
            if service_id not in worker_ids:
                ready.append(service_id)
                continue
            if service_id in ready_worker_ids:
                ready.append(service_id)
        return ready

    def inactive_service_ids(self) -> List[str]:
        """Return worker service IDs that are currently inactive."""
        inactive: List[str] = []
        for stage in self.stages():
            inactive.extend(self.inactive_workers(stage))
        return inactive

    def base_attrs_for(self, service_id: str) -> Dict[str, Any]:
        """Return the declared service attributes before runtime activation changes."""

        if service_id in self._base_node_attrs:
            return dict(self._base_node_attrs[service_id])
        if service_id not in self.application.nodes:
            return {}
        return dict(self.application.nodes[service_id])

    def snapshots(self) -> Dict[str, WorkerPoolSnapshot]:
        """Build worker-pool snapshots for controller state."""
        self._refresh_worker_runtime_states()
        result: Dict[str, WorkerPoolSnapshot] = {}
        for stage, workers in self._worker_pools.items():
            active_workers = self.active_workers(stage)
            inactive_workers = self.inactive_workers(stage)
            ready_workers = self.ready_workers(stage)
            starting_workers = self.starting_workers(stage)
            result[stage] = WorkerPoolSnapshot(
                stage=stage,
                min_workers=self._min_workers.get(stage, 0),
                max_workers=len(workers),
                active_workers=len(active_workers),
                total_workers=len(workers),
                active_worker_ids=list(active_workers),
                inactive_worker_ids=list(inactive_workers),
                ready_workers=len(ready_workers),
                ready_worker_ids=list(ready_workers),
                starting_workers=len(starting_workers),
                starting_worker_ids=list(starting_workers),
            )
        return result

    def _worker_is_ready(self, service_id: str) -> bool:
        """Return whether a worker has reached its ready time."""
        ready_at_s = self._worker_ready_at_s.get(service_id, float("-inf"))
        return float(self._time_fn()) >= ready_at_s

    def _refresh_worker_runtime_states(self) -> None:
        """Refresh runtime flags on worker service nodes."""
        for stage, workers in self._worker_pools.items():
            active_count = self._active_workers.get(stage, 0)
            for index, worker_id in enumerate(workers):
                active = index < active_count
                ready = active and self._worker_is_ready(worker_id)
                self._set_worker_state(worker_id, active=active, ready=ready)

    def _set_worker_state(self, service_id: str, active: bool, ready: bool) -> None:
        """Apply active and ready flags to a worker node."""
        if service_id not in self.application.nodes:
            return

        node_attrs = self.application.nodes[service_id]
        base_attrs = self._base_node_attrs.get(service_id, {})

        for key, value in base_attrs.items():
            if isinstance(value, bool):
                node_attrs[key] = value
            elif isinstance(value, (int, float)):
                if active:
                    node_attrs[key] = value
                else:
                    node_attrs[key] = self._idle_epsilon if value > 0 else value
            else:
                node_attrs[key] = value

        node_attrs["worker_active"] = active
        node_attrs["worker_ready"] = ready
        node_attrs["worker_starting"] = active and not ready
        node_attrs["active"] = active

        service = self.application.services.get(service_id)
        if isinstance(service, SkeletonService):
            service.set_active(active)
