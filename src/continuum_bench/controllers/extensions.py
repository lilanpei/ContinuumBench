"""Optional extension point for user-defined controllers.

The benchmark ships a fixed controller set (see ``ContinuumBenchRunner._build_controller``).
To evaluate a controller of your own without editing the runner, add a module
``continuum_bench/controllers/extensions_local.py`` exposing::

    def build(name, config, runner): ...

It receives the controller name from the config, the full run config, and the runner
instance -- the latter so a composite controller can build sub-controllers, or a
runner-coupled controller can read run context. Return the controller instance, or
``None`` to let the caller report an unknown controller name.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_extension_controller(name: str, config: Dict[str, Any], runner: Any) -> Optional[Any]:
    """Build ``name`` from the local extension module, or None if unavailable/unknown."""

    try:
        from continuum_bench.controllers import extensions_local
    except ImportError:
        return None

    return extensions_local.build(name, config, runner)
