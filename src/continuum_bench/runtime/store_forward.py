"""Store-and-forward buffer management for intermittent connectivity."""

from __future__ import annotations

import copy
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional


class BufferPriority(Enum):
    """Priority levels for buffered data."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class BufferedItem:
    """A single item in the store-and-forward buffer."""

    data: Any
    priority: BufferPriority
    timestamp: float = 0.0
    size_bytes: int = 0
    retries: int = 0
    max_retries: int = 3
    ttl: float = 3600.0
    time_fn: Callable[[], float] = field(default=time.time, repr=False, compare=False)

    @property
    def is_expired(self) -> bool:
        """Return whether the item TTL has elapsed."""

        return (self.time_fn() - self.timestamp) > self.ttl

    @property
    def age(self) -> float:
        """Return the item age in seconds."""

        return self.time_fn() - self.timestamp


class StoreForwardBuffer:
    """Priority-aware buffer for intermittent-link store-and-forward delivery."""

    def __init__(
        self,
        max_size_bytes: int = 128 * 1024 * 1024,
        max_items: int = 10000,
        on_overflow: str = "drop_oldest_low",
        time_fn: Optional[Callable[[], float]] = None,
    ):
        """Initialize buffer capacity limits and statistics."""

        self.max_size_bytes = max_size_bytes
        self.max_items = max_items
        self.on_overflow = on_overflow
        self._time_fn = time_fn or time.time

        self._queues: Dict[BufferPriority, Deque[BufferedItem]] = {
            priority: deque() for priority in BufferPriority
        }
        self._current_size_bytes = 0
        self._total_items = 0
        self.stats = {
            "items_buffered": 0,
            "items_transmitted": 0,
            "items_dropped": 0,
            "items_expired": 0,
            "bytes_buffered": 0,
            "bytes_transmitted": 0,
            "bytes_dropped": 0,
            "bytes_expired": 0,
            "priority": {
                priority.name: {
                    "items_buffered": 0,
                    "items_transmitted": 0,
                    "items_dropped": 0,
                    "items_expired": 0,
                    "bytes_buffered": 0,
                    "bytes_transmitted": 0,
                    "bytes_dropped": 0,
                    "bytes_expired": 0,
                }
                for priority in BufferPriority
            },
        }

    def enqueue(
        self,
        data: Any,
        priority: BufferPriority = BufferPriority.NORMAL,
        size_bytes: int = 0,
        ttl: float = 3600.0,
        count_buffered: bool = True,
    ) -> bool:
        """Add an item to the buffer.

        Set ``count_buffered=False`` to re-insert an item that was already counted as buffered
        (e.g. a failed delivery attempt being returned to the queue) without double-counting it
        in ``items_buffered``/``bytes_buffered``.
        """

        item = BufferedItem(
            data=data,
            priority=priority,
            size_bytes=size_bytes,
            ttl=ttl,
            timestamp=self._time_fn(),
            time_fn=self._time_fn,
        )

        if self._total_items >= self.max_items or self._current_size_bytes + size_bytes > self.max_size_bytes:
            if not self._handle_overflow(size_bytes, priority):
                self.stats["items_dropped"] += 1
                self.stats["bytes_dropped"] += size_bytes
                self._priority_stats(priority)["items_dropped"] += 1
                self._priority_stats(priority)["bytes_dropped"] += size_bytes
                return False

        self._queues[priority].append(item)
        self._current_size_bytes += size_bytes
        self._total_items += 1
        if count_buffered:
            self.stats["items_buffered"] += 1
            self.stats["bytes_buffered"] += size_bytes
            self._priority_stats(priority)["items_buffered"] += 1
            self._priority_stats(priority)["bytes_buffered"] += size_bytes
        return True

    def dequeue(self, max_bytes: Optional[int] = None, count_transmitted: bool = True) -> List[BufferedItem]:
        """Retrieve items from the buffer in priority order.

        Set ``count_transmitted=False`` when popping items for a delivery *attempt* that may fail
        (and re-buffer); the caller then records actual sends via :meth:`mark_transmitted`, so an
        item that is dequeued but not sent is not counted as transmitted.
        """

        items: List[BufferedItem] = []
        bytes_retrieved = 0

        self._clean_expired()

        for priority in BufferPriority:
            queue = self._queues[priority]
            while queue:
                if max_bytes and bytes_retrieved + queue[0].size_bytes > max_bytes:
                    break
                item = queue.popleft()
                items.append(item)
                bytes_retrieved += item.size_bytes
                self._current_size_bytes -= item.size_bytes
                self._total_items -= 1
                if count_transmitted:
                    self.stats["items_transmitted"] += 1
                    self.stats["bytes_transmitted"] += item.size_bytes
                    self._priority_stats(item.priority)["items_transmitted"] += 1
                    self._priority_stats(item.priority)["bytes_transmitted"] += item.size_bytes

        return items

    def mark_transmitted(self, item: BufferedItem) -> None:
        """Record that a dequeued item was actually transmitted over the link."""

        self.stats["items_transmitted"] += 1
        self.stats["bytes_transmitted"] += item.size_bytes
        self._priority_stats(item.priority)["items_transmitted"] += 1
        self._priority_stats(item.priority)["bytes_transmitted"] += item.size_bytes

    def peek(self, priority: Optional[BufferPriority] = None) -> Optional[BufferedItem]:
        """Peek at the next item without removing it."""

        if priority is not None:
            queue = self._queues[priority]
            return queue[0] if queue else None

        for item_priority in BufferPriority:
            if self._queues[item_priority]:
                return self._queues[item_priority][0]
        return None

    def requeue(self, item: BufferedItem) -> bool:
        """Re-add a failed transmission item with incremented retry count."""

        item.retries += 1
        if item.retries > item.max_retries:
            self.stats["items_dropped"] += 1
            self.stats["bytes_dropped"] += item.size_bytes
            self._priority_stats(item.priority)["items_dropped"] += 1
            self._priority_stats(item.priority)["bytes_dropped"] += item.size_bytes
            return False

        self._queues[item.priority].appendleft(item)
        self._current_size_bytes += item.size_bytes
        self._total_items += 1
        return True

    def _handle_overflow(self, needed_bytes: int, incoming_priority: BufferPriority) -> bool:
        """Handle buffer overflow using the configured policy."""

        if needed_bytes > self.max_size_bytes:
            return False

        if self.on_overflow == "reject":
            return False

        if self.on_overflow == "drop_oldest_low":
            for priority in reversed(list(BufferPriority)):
                if priority.value < incoming_priority.value:
                    continue
                queue = self._queues[priority]
                while queue and (
                    self._total_items >= self.max_items
                    or self._current_size_bytes + needed_bytes > self.max_size_bytes
                ):
                    dropped = queue.popleft()
                    self._current_size_bytes -= dropped.size_bytes
                    self._total_items -= 1
                    self.stats["items_dropped"] += 1
                    self.stats["bytes_dropped"] += dropped.size_bytes
                    self._priority_stats(dropped.priority)["items_dropped"] += 1
                    self._priority_stats(dropped.priority)["bytes_dropped"] += dropped.size_bytes
            return self._total_items < self.max_items and self._current_size_bytes + needed_bytes <= self.max_size_bytes

        if self.on_overflow == "drop_newest":
            return False

        return False

    def clean_expired(self) -> None:
        """Public cleanup hook for deterministic testing and external control."""

        self._clean_expired()

    def _clean_expired(self) -> None:
        """Remove expired items from all queues."""

        for priority in BufferPriority:
            queue = self._queues[priority]
            expired_count = 0
            expired_bytes = 0
            new_queue: Deque[BufferedItem] = deque()

            for item in queue:
                if item.is_expired:
                    expired_count += 1
                    expired_bytes += item.size_bytes
                else:
                    new_queue.append(item)

            self._queues[priority] = new_queue
            self._total_items -= expired_count
            self._current_size_bytes -= expired_bytes
            self.stats["items_expired"] += expired_count
            self.stats["bytes_expired"] += expired_bytes
            self._priority_stats(priority)["items_expired"] += expired_count
            self._priority_stats(priority)["bytes_expired"] += expired_bytes

    @property
    def size(self) -> int:
        """Return the number of buffered items."""

        return self._total_items

    @property
    def size_bytes(self) -> int:
        """Return the total buffered bytes."""

        return self._current_size_bytes

    @property
    def is_empty(self) -> bool:
        """Return whether the buffer is empty."""

        return self._total_items == 0

    @property
    def utilization(self) -> float:
        """Return current byte utilization."""

        return self._current_size_bytes / self.max_size_bytes if self.max_size_bytes > 0 else 0.0

    def get_queue_sizes(self) -> Dict[str, int]:
        """Return queued item counts by priority."""

        return {priority.name: len(self._queues[priority]) for priority in BufferPriority}

    def get_stats(self) -> Dict[str, Any]:
        """Return a copy of cumulative buffer statistics."""

        return copy.deepcopy(self.stats)

    def _priority_stats(self, priority: BufferPriority) -> Dict[str, int]:
        """Return the stats bucket for one priority."""

        return self.stats["priority"][priority.name]


__all__ = ["BufferPriority", "BufferedItem", "StoreForwardBuffer"]
