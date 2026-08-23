"""Runtime skeleton helpers built on top of the store-and-forward buffer."""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from typing import Any, Dict, List, Literal, Optional

from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.runtime.store_forward import BufferPriority, StoreForwardBuffer

ensure_eclypse_on_path()
try:
    from eclypse.remote.service.service import Service
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Eclypse is required for ContinuumBench runtime services. "
        "Install eclypse or set ECLYPSE_HOME to a local checkout."
    ) from exc


class SkeletonRole(Enum):
    SOURCE = auto()
    TRANSFORM = auto()
    SINK = auto()


class SkeletonService(Service):
    """Generic source/transform/sink runtime service with buffering support."""

    def __init__(
        self,
        service_id: str,
        role: SkeletonRole,
        profile: Dict[str, Any],
        comm_interface: Literal["mpi", "rest"] = "mpi",
    ):
        """Initialize a skeleton service and its buffer."""

        super().__init__(service_id, comm_interface=comm_interface, store_step=True)
        self.role = role
        self.profile = profile
        self.targets: List[str] = []
        self._task_counter = 0
        self.worker_group: Optional[str] = profile.get("worker_group")
        self.worker_index: Optional[int] = profile.get("worker_index")
        self._active = bool(profile.get("active", True))

        buffer_config = profile.get("buffer", {})
        self._buffer = StoreForwardBuffer(
            max_size_bytes=buffer_config.get("max_size_bytes", 128 * 1024 * 1024),
            max_items=buffer_config.get("max_items", 10000),
            on_overflow=buffer_config.get("on_overflow", "drop_oldest_low"),
        )
        self._link_available = True

    def add_target(self, target_id: str) -> None:
        """Register a downstream target once."""

        if target_id not in self.targets:
            self.targets.append(target_id)

    async def step(self):
        """Run one role-specific service step."""

        if not self._active and self.role == SkeletonRole.TRANSFORM:
            await asyncio.sleep(0)
            return None
        if self.role == SkeletonRole.SOURCE:
            return await self._step_source()
        if self.role == SkeletonRole.TRANSFORM:
            return await self._step_transform()
        if self.role == SkeletonRole.SINK:
            return await self._step_sink()
        return None

    def set_active(self, active: bool) -> None:
        """Enable or disable transform work."""

        self._active = bool(active)

    @property
    def is_active(self) -> bool:
        """Return whether the service is active."""

        return self._active

    async def _step_source(self):
        """Generate and dispatch one source task."""

        arrival_rate = self.profile.get("arrival_rate", 1.0)
        await asyncio.sleep(1.0 / arrival_rate)

        self._task_counter += 1
        priority = "high" if self._task_counter % 10 == 0 else "low"

        task = {
            "id": f"{self.id}-{self._task_counter}",
            "timestamp": time.time(),
            "data_size": self.profile.get("data_size", 1.0),
            "priority": priority,
            "type": self.profile.get("task_type", "generic"),
            "role": "SOURCE",
        }

        await self._dispatch(task)
        return task

    async def _step_transform(self):
        """Receive, transform, and dispatch one task."""

        if self._comm_interface == "mpi":
            task = await self.mpi.recv()
        else:
            raise NotImplementedError("REST receive not implemented.")

        processing_time = self.profile.get("processing_time", 1.0)
        await asyncio.sleep(processing_time)

        alpha = self.profile.get("alpha", 1.0)
        task["data_size"] *= alpha
        task["role"] = "TRANSFORM"

        await self._dispatch(task)
        return task

    def set_link_available(self, available: bool) -> None:
        """Set current downstream runtime contact reachability."""

        self._link_available = available

    async def flush_buffer(self, max_bytes: Optional[int] = None) -> None:
        """Send buffered items while connectivity is available."""

        if not self._link_available:
            return

        items = self._buffer.dequeue(max_bytes=max_bytes)
        for item in items:
            try:
                await self._send_to_targets(item.data, [item.data.get("_target", self.targets[0])])
            except Exception:
                self._buffer.requeue(item)

    async def _dispatch(self, task: Dict[str, Any]) -> None:
        """Route a task to targets or store it locally."""

        if not self.targets:
            return

        policy = self.profile.get("routing_policy", "round-robin")

        if policy == "triage":
            if task.get("priority") == "high":
                targets_to_send = [self.targets[0]]
                buffer_priority = BufferPriority.HIGH
            else:
                targets_to_send = self.targets[1:] if len(self.targets) > 1 else self.targets
                buffer_priority = BufferPriority.LOW
        elif policy == "broadcast":
            targets_to_send = self.targets
            buffer_priority = BufferPriority.NORMAL
        else:
            targets_to_send = [self.targets[self._task_counter % len(self.targets)]]
            if self.role != SkeletonRole.SOURCE:
                self._task_counter += 1
            buffer_priority = BufferPriority.NORMAL

        if not self._link_available:
            for target in targets_to_send:
                task_copy = task.copy()
                task_copy["_target"] = target
                self._buffer.enqueue(
                    data=task_copy,
                    priority=buffer_priority,
                    size_bytes=int(task.get("data_size", 1.0) * 1024),
                    ttl=self.profile.get("buffer_ttl", 3600.0),
                )
            return

        await self.flush_buffer()
        await self._send_to_targets(task, targets_to_send)

    async def _send_to_targets(self, task: Dict[str, Any], targets: List[str]) -> None:
        """Send a task to each selected target."""

        for target in targets:
            if self._comm_interface == "mpi":
                await self.mpi.send(target, task)

    async def _step_sink(self):
        """Receive one task and compute sink metrics."""

        if self._comm_interface == "mpi":
            task = await self.mpi.recv()
        else:
            raise NotImplementedError("REST receive not implemented in generic skeleton.")

        ct = time.time() - task["timestamp"]
        deadline = self.profile.get("deadline", 30.0)
        sla_violation = ct > deadline

        return {
            "task_id": task["id"],
            "completion_time": ct,
            "sla_violation": sla_violation,
            "priority": task.get("priority", "normal"),
            "data_size": task.get("data_size", 0),
        }

    def get_buffer_stats(self) -> Dict[str, Any]:
        """Return current service and buffer statistics."""

        return {
            "active": self._active,
            "link_available": self._link_available,
            "buffer_size": self._buffer.size,
            "buffer_bytes": self._buffer.size_bytes,
            "buffer_utilization": self._buffer.utilization,
            "queue_sizes": self._buffer.get_queue_sizes(),
            "stats": self._buffer.get_stats(),
        }


__all__ = ["SkeletonRole", "SkeletonService"]
