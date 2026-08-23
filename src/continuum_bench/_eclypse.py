"""Helpers for locating the Eclypse dependency."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


def ensure_eclypse_on_path() -> None:
    """Add an optional local Eclypse checkout to import and executable paths."""

    eclypse_home = os.environ.get("ECLYPSE_HOME")
    if not eclypse_home:
        return

    home_path = Path(eclypse_home)
    if (home_path / "eclypse").is_dir():
        home_str = str(home_path)
        if home_str not in sys.path:
            sys.path.insert(0, home_str)

    bin_dir = home_path / "bin"
    if not bin_dir.is_dir():
        return

    bin_path = str(bin_dir)
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if bin_path not in path_parts:
        os.environ["PATH"] = os.pathsep.join([bin_path, *path_parts]) if path_parts else bin_path


def ensure_eclypse_log_level() -> None:
    """Register Eclypse's custom Loguru level when no SimulationConfig exists."""

    try:
        logger.level("ECLYPSE")
    except ValueError:
        logger.level("ECLYPSE", no=15, color="<b><magenta>", icon="E")
