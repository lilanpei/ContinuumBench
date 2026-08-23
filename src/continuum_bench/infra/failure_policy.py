from __future__ import annotations

from typing import Any, Iterable


class NodeFailureAvailabilityUpdatePolicy:
    """Eclypse node update-policy adapter for benchmark node failures."""

    def __init__(self) -> None:
        self.node_down: set[str] = set()
        self._node_restore: dict[str, Any] = {}

    def set_failures(
        self,
        *,
        node_down: Iterable[str],
    ) -> None:
        """Set the node failure set to apply on the next graph evolution."""

        self.node_down = {str(node_id) for node_id in node_down}

    def clear_failures(self) -> None:
        """Clear pending failure events without changing graph attributes."""

        self.node_down.clear()

    def restore(self, infrastructure: Any) -> None:
        """Remove the previous benchmark failure overlay from the graph."""
        self._apply_restore(infrastructure.nodes, self._node_restore)
        self._node_restore.clear()

    def apply_nodes(self, nodes: Any) -> None:
        """Eclypse node update policy: mark failed nodes unavailable."""

        for node_id in sorted(self.node_down):
            if node_id not in nodes:
                continue
            if node_id not in self._node_restore:
                self._node_restore[node_id] = nodes[node_id].get("availability", 1.0)
            nodes[node_id]["availability"] = 0.0

    def _apply_restore(self, view: Any, restore_map: dict[Any, Any]) -> None:
        """Restore a cached availability map into a graph view."""

        for key, availability in restore_map.items():
            try:
                attrs = view[key]
            except Exception:
                continue
            attrs["availability"] = availability
