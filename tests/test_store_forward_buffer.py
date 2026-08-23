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
