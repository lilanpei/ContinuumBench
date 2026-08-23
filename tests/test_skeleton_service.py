from __future__ import annotations

from typing import Any

import pytest

import continuum_bench.runtime.skeleton as skeleton_runtime
from continuum_bench.runtime.skeleton import SkeletonRole, SkeletonService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_source_round_robin_uses_one_counter_increment_per_generated_task(monkeypatch):
    sends: list[tuple[str, list[str]]] = []

    async def fake_sleep(_delay: float) -> None:
        return None

    async def fake_send_to_targets(self, task: dict[str, Any], targets: list[str]) -> None:
        del self
        sends.append((task["id"], list(targets)))

    monkeypatch.setattr(skeleton_runtime.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(SkeletonService, "_send_to_targets", fake_send_to_targets)

    service = SkeletonService(
        service_id="source",
        role=SkeletonRole.SOURCE,
        profile={"arrival_rate": 1.0, "routing_policy": "round-robin"},
    )
    service.application_id = "test_app"
    service.add_target("target-a")
    service.add_target("target-b")

    first = await service.step()
    second = await service.step()

    assert first["id"] == "source-1"
    assert second["id"] == "source-2"
    assert sends == [("source-1", ["target-b"]), ("source-2", ["target-a"])]
