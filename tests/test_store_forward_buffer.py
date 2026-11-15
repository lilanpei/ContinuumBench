from __future__ import annotations

from dataclasses import dataclass

from continuum_bench.runtime.store_forward import BufferPriority, StoreForwardBuffer


@dataclass
class ManualClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += float(delta)


def test_ttl_expiration_with_deterministic_clock():
    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=16, time_fn=clock.now)

    buffer.enqueue(data={"id": "a"}, priority=BufferPriority.NORMAL, size_bytes=10, ttl=1.0)
    buffer.enqueue(data={"id": "b"}, priority=BufferPriority.NORMAL, size_bytes=10, ttl=5.0)

    clock.advance(2.0)
    buffer.clean_expired()

    assert buffer.size == 1
    assert buffer.stats["items_expired"] == 1
    assert buffer.stats["bytes_expired"] == 10


def test_priority_dequeue_order():
    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=16, time_fn=clock.now)

    buffer.enqueue(data={"id": "low"}, priority=BufferPriority.LOW, size_bytes=1, ttl=10.0)
    buffer.enqueue(data={"id": "normal"}, priority=BufferPriority.NORMAL, size_bytes=1, ttl=10.0)
    buffer.enqueue(data={"id": "high"}, priority=BufferPriority.HIGH, size_bytes=1, ttl=10.0)
    buffer.enqueue(data={"id": "critical"}, priority=BufferPriority.CRITICAL, size_bytes=1, ttl=10.0)

    items = buffer.dequeue()
    order = [item.data["id"] for item in items]

    assert order == ["critical", "high", "normal", "low"]


def test_overflow_drop_oldest_low_policy():
    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=2, on_overflow="drop_oldest_low", time_fn=clock.now)

    assert buffer.enqueue(data={"id": "low-1"}, priority=BufferPriority.LOW, size_bytes=2, ttl=10.0)
    assert buffer.enqueue(data={"id": "low-2"}, priority=BufferPriority.LOW, size_bytes=2, ttl=10.0)
    assert buffer.enqueue(data={"id": "high-1"}, priority=BufferPriority.HIGH, size_bytes=2, ttl=10.0)

    items = buffer.dequeue()
    ids = [item.data["id"] for item in items]

    assert "low-1" not in ids
    assert "low-2" in ids
    assert "high-1" in ids
    assert buffer.stats["items_dropped"] == 1


def test_enqueue_rejects_item_larger_than_buffer_capacity():
    clock = ManualClock()
    buffer = StoreForwardBuffer(max_size_bytes=4, max_items=4, on_overflow="drop_oldest_low", time_fn=clock.now)

    accepted = buffer.enqueue(data={"id": "oversized"}, priority=BufferPriority.NORMAL, size_bytes=5, ttl=10.0)

    assert accepted is False
    assert buffer.size == 0
    assert buffer.size_bytes == 0
    assert buffer.stats["items_dropped"] == 1
    assert buffer.stats["bytes_dropped"] == 5


def test_drop_oldest_low_rejects_lower_priority_item_before_evicting_higher_priority_data():
    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=1, on_overflow="drop_oldest_low", time_fn=clock.now)

    assert buffer.enqueue(data={"id": "high"}, priority=BufferPriority.HIGH, size_bytes=1, ttl=10.0)

    accepted = buffer.enqueue(data={"id": "low"}, priority=BufferPriority.LOW, size_bytes=1, ttl=10.0)

    items = buffer.dequeue()

    assert accepted is False
    assert [item.data["id"] for item in items] == ["high"]
    assert buffer.stats["items_dropped"] == 1


def test_oversized_item_is_released_instead_of_stalling_forever():
    """An item bigger than the whole per-epoch budget must still make progress.

    Without head-of-line unblocking, ``bytes_retrieved + size > max_bytes`` would hold in
    every epoch for such an item, so it would never be dequeued and the transfer would stall
    silently (no error, no drop: the task would never arrive).
    """

    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=16, time_fn=clock.now)
    buffer.enqueue(data={"id": "huge"}, priority=BufferPriority.NORMAL, size_bytes=100, ttl=10.0)

    items = buffer.dequeue(max_bytes=10)  # budget far smaller than the item

    assert [item.data["id"] for item in items] == ["huge"]
    assert buffer.size == 0


def test_oversized_item_does_not_drag_extra_items_past_the_budget():
    """Head-of-line unblocking releases the oversized item *alone*, not a free-for-all."""

    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=16, time_fn=clock.now)
    buffer.enqueue(data={"id": "huge"}, priority=BufferPriority.NORMAL, size_bytes=100, ttl=10.0)
    buffer.enqueue(data={"id": "next"}, priority=BufferPriority.NORMAL, size_bytes=1, ttl=10.0)

    items = buffer.dequeue(max_bytes=10)

    assert [item.data["id"] for item in items] == ["huge"]
    assert buffer.size == 1


def test_items_within_budget_are_unaffected():
    """Items that fit keep the original semantics: fill up to the budget, then stop."""

    clock = ManualClock()
    buffer = StoreForwardBuffer(max_items=16, time_fn=clock.now)
    for idx in range(4):
        buffer.enqueue(data={"id": str(idx)}, priority=BufferPriority.NORMAL, size_bytes=4, ttl=10.0)

    items = buffer.dequeue(max_bytes=10)  # fits two 4-byte items, not three

    assert [item.data["id"] for item in items] == ["0", "1"]
    assert buffer.size == 2
