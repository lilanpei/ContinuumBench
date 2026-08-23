"""Runtime package."""

from continuum_bench.runtime.store_forward import BufferPriority, BufferedItem, StoreForwardBuffer

__all__ = [
    "BufferPriority",
    "BufferedItem",
    "StoreForwardBuffer",
    "SkeletonRole",
    "SkeletonService",
]


def __getattr__(name):
    # Lazily expose the ECLYPSE-backed skeleton service so that eclypse-free runtime modules
    # (e.g. store_forward) stay importable without the external eclypse substrate installed.
    if name in ("SkeletonRole", "SkeletonService"):
        from continuum_bench.runtime import skeleton

        return getattr(skeleton, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
