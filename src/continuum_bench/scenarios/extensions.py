"""Optional extension point for user-defined scenarios.

The benchmark ships a fixed scenario set (see ``build_scenario``). To evaluate a scenario of
your own without editing the dispatcher, add a module
``continuum_bench/scenarios/extensions_local.py`` exposing::

    def build(normalized_name, config, seed): ...

Return a ``ScenarioBuild``, or ``None`` to let the caller report an unsupported scenario.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_extension_scenario(normalized_name: str, config: Dict[str, Any], seed: int) -> Optional[Any]:
    """Build ``normalized_name`` from the local extension module, or None if unavailable/unknown."""

    try:
        from continuum_bench.scenarios import extensions_local
    except ImportError:
        return None

    return extensions_local.build(normalized_name, config, seed)
