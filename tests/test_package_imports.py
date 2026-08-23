import ast
import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest


try:
    ECLYPSE_AVAILABLE = importlib.util.find_spec("eclypse") is not None
except Exception:
    ECLYPSE_AVAILABLE = False
requires_eclypse = pytest.mark.skipif(
    not ECLYPSE_AVAILABLE, reason="requires the external eclypse substrate"
)


CORE_DOCSTRING_TARGETS = [
    "src/continuum_bench/cli.py",
    "src/continuum_bench/runner.py",
    "src/continuum_bench/calibration_profiles.py",
    "src/continuum_bench/infra/contact_schedule.py",
    "src/continuum_bench/infra/generators.py",
    "src/continuum_bench/sweeps/manifest.py",
    "src/continuum_bench/sweeps/runner.py",
]

CONTROLLER_DOCSTRING_TARGETS = [
    "src/continuum_bench/controllers/interfaces.py",
    "src/continuum_bench/controllers/placement_only.py",
    "src/continuum_bench/controllers/separated_loops.py",
    "src/continuum_bench/placement/strategies.py",
    "src/continuum_bench/oracles/milp_oracle.py",
]

RUNTIME_DOCSTRING_TARGETS = [
    "src/continuum_bench/runtime/skeleton.py",
    "src/continuum_bench/runtime/store_forward.py",
    "src/continuum_bench/metrics/logger.py",
    "src/continuum_bench/metrics/summary.py",
    "src/continuum_bench/workload/mmpp.py",
    "src/continuum_bench/scenarios/__init__.py",
    "src/continuum_bench/scenarios/eo_dual_stream.py",
    "src/continuum_bench/scenarios/driving_v2x.py",
    "src/continuum_bench/scenarios/split_inference.py",
    "src/continuum_bench/scenarios/industrial_iot.py",
]

TOOLING_DOCSTRING_TARGETS = [
    "analysis/aggregate_runs.py",
    "calibration/fit_profiles.py",
    "calibration/validate_rankings.py",
]


def _missing_docstrings(module_path: Path) -> list[str]:
    """Return undocumented function and method names from a Python module.

    The readability pass requires docstrings on every production function in the
    targeted modules, including helpers nested under control flow or inside
    other functions.
    """

    module = ast.parse(module_path.read_text(encoding="utf-8"))
    missing: list[str] = []

    def visit_node(node: ast.AST, class_name: str | None = None) -> None:
        """Walk the AST and record defs that do not define docstrings."""

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node) is None:
                missing.append(f"{class_name}.{node.name}" if class_name else node.name)

        next_class_name = node.name if isinstance(node, ast.ClassDef) else class_name
        for child in ast.iter_child_nodes(node):
            visit_node(child, class_name=next_class_name)

    visit_node(module)
    return missing


def test_missing_docstrings_traverses_nested_control_flow_blocks_including_match_cases(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "sample_module.py"
    module_path.write_text(
        "\n".join(
            [
                "if True:",
                "    def top_level():",
                "        pass",
                "",
                "match 0:",
                "    case 0:",
                "        def matched_top_level():",
                "            pass",
                "",
                "try:",
                "    import optional_dependency",
                "except Exception:",
                "    class PlacementStrategy:",
                "        if True:",
                "            def is_feasible(self):",
                "                pass",
                "",
                "def outer():",
                "    def inner():",
                "        pass",
                "    match 0:",
                "        case 0:",
                "            def matched_inner():",
                "                pass",
                "",
                "class MatchWrapper:",
                "    match 0:",
                "        case 0:",
                "            def matched_method(self):",
                "                pass",
                "",
                "class Wrapper:",
                "    def method(self):",
                "        def helper():",
                "            pass",
                "        match 0:",
                "            case 0:",
                "                def match_helper():",
                "                    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert _missing_docstrings(module_path) == [
        "top_level",
        "matched_top_level",
        "PlacementStrategy.is_feasible",
        "outer",
        "inner",
        "matched_inner",
        "MatchWrapper.matched_method",
        "Wrapper.method",
        "Wrapper.helper",
        "Wrapper.match_helper",
    ]


@requires_eclypse
def test_package_smoke_imports() -> None:
    package = importlib.import_module("continuum_bench")
    cli = importlib.import_module("continuum_bench.cli")
    eclypse = importlib.import_module("continuum_bench._eclypse")
    controllers = importlib.import_module("continuum_bench.controllers")
    scenarios = importlib.import_module("continuum_bench.scenarios")
    infra = importlib.import_module("continuum_bench.infra")
    workload = importlib.import_module("continuum_bench.workload")
    metrics = importlib.import_module("continuum_bench.metrics")
    runtime = importlib.import_module("continuum_bench.runtime")
    placement = importlib.import_module("continuum_bench.placement")

    assert package.__name__ == "continuum_bench"
    assert cli.__name__ == "continuum_bench.cli"
    assert eclypse.__name__ == "continuum_bench._eclypse"
    assert controllers.__name__ == "continuum_bench.controllers"
    assert scenarios.__name__ == "continuum_bench.scenarios"
    assert infra.__name__ == "continuum_bench.infra"
    assert workload.__name__ == "continuum_bench.workload"
    assert metrics.__name__ == "continuum_bench.metrics"
    assert runtime.__name__ == "continuum_bench.runtime"
    assert placement.__name__ == "continuum_bench.placement"
    assert callable(eclypse.ensure_eclypse_on_path)
    assert not hasattr(infra, "apply_schedule_to_edges")


def test_cli_exposes_main_entrypoint() -> None:
    cli = importlib.import_module("continuum_bench.cli")

    assert callable(cli.main)


def test_ensure_eclypse_on_path_ignores_missing_home(monkeypatch, tmp_path) -> None:
    eclypse = importlib.import_module("continuum_bench._eclypse")
    missing_home = tmp_path / "missing-eclypse"

    monkeypatch.setenv("ECLYPSE_HOME", str(missing_home))
    monkeypatch.setenv("PATH", "/usr/bin")

    eclypse.ensure_eclypse_on_path()

    assert os.environ["PATH"] == "/usr/bin"


def test_ensure_eclypse_on_path_prepends_existing_bin(monkeypatch, tmp_path) -> None:
    eclypse = importlib.import_module("continuum_bench._eclypse")
    eclypse_home = tmp_path / "eclypse"
    bin_dir = eclypse_home / "bin"
    bin_dir.mkdir(parents=True)

    monkeypatch.setenv("ECLYPSE_HOME", str(eclypse_home))
    monkeypatch.setenv("PATH", "/usr/bin")

    eclypse.ensure_eclypse_on_path()
    eclypse.ensure_eclypse_on_path()

    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert os.environ["PATH"].split(os.pathsep).count(str(bin_dir)) == 1


def test_ensure_eclypse_on_path_adds_checkout_to_python_import_path(monkeypatch, tmp_path) -> None:
    eclypse = importlib.import_module("continuum_bench._eclypse")
    eclypse_home = tmp_path / "eclypse"
    package_dir = eclypse_home / "eclypse"
    package_dir.mkdir(parents=True)

    monkeypatch.setenv("ECLYPSE_HOME", str(eclypse_home))
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != str(eclypse_home)])

    eclypse.ensure_eclypse_on_path()
    eclypse.ensure_eclypse_on_path()

    assert sys.path[0] == str(eclypse_home)
    assert sys.path.count(str(eclypse_home)) == 1


def test_substrate_builders_require_eclypse_instead_of_local_graph_fallbacks(monkeypatch) -> None:
    monkeypatch.delenv("ECLYPSE_HOME", raising=False)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if "eclypse" not in path])
    sys.modules.pop("eclypse", None)
    sys.modules.pop("eclypse.graph", None)

    generators = importlib.reload(importlib.import_module("continuum_bench.infra.generators"))
    scenarios = importlib.reload(importlib.import_module("continuum_bench.scenarios"))

    try:
        with pytest.raises(ModuleNotFoundError, match="Eclypse is required"):
            generators._build_infrastructure("requires-eclypse", seed=1)
        with pytest.raises(ModuleNotFoundError, match="Eclypse is required"):
            scenarios._build_application("requires-eclypse")
    finally:
        importlib.reload(generators)
        importlib.reload(scenarios)


@requires_eclypse
def test_runtime_reexports_skeleton_types() -> None:
    from continuum_bench.runtime import SkeletonRole, SkeletonService

    assert SkeletonRole.__name__ == "SkeletonRole"
    assert SkeletonService.__name__ == "SkeletonService"


@requires_eclypse
def test_placement_package_reexports_strategies_for_future_controllers() -> None:
    from continuum_bench.placement import MinCTStrategy, RoundRobinStrategy

    assert MinCTStrategy.__name__ == "MinCTStrategy"
    assert RoundRobinStrategy.__name__ == "RoundRobinStrategy"


@requires_eclypse
def test_controllers_package_reexports_ported_controller_types() -> None:
    from continuum_bench.controllers import (
        Controller,
        ControllerAction,
        ControllerState,
        HPAController,
        KEDAController,
        PlacementOnlyController,
        RLMetaController,
        ScaleAction,
        SeparatedLoopsController,
        WorkerPoolManager,
        WorkerPoolSnapshot,
    )

    assert Controller.__name__ == "Controller"
    assert ControllerAction.__name__ == "ControllerAction"
    assert ControllerState.__name__ == "ControllerState"
    assert HPAController.__name__ == "HPAController"
    assert KEDAController.__name__ == "KEDAController"
    assert PlacementOnlyController.__name__ == "PlacementOnlyController"
    assert RLMetaController.__name__ == "RLMetaController"
    assert ScaleAction.__name__ == "ScaleAction"
    assert SeparatedLoopsController.__name__ == "SeparatedLoopsController"
    assert WorkerPoolManager.__name__ == "WorkerPoolManager"
    assert WorkerPoolSnapshot.__name__ == "WorkerPoolSnapshot"


def test_core_modules_define_docstrings_for_functions_and_methods() -> None:
    root = Path(__file__).resolve().parents[1]
    missing_by_module = {
        relative_path: _missing_docstrings(root / relative_path)
        for relative_path in CORE_DOCSTRING_TARGETS
    }

    missing_by_module = {
        relative_path: missing
        for relative_path, missing in missing_by_module.items()
        if missing
    }

    assert missing_by_module == {}


def test_controller_modules_define_docstrings_for_functions_and_methods() -> None:
    root = Path(__file__).resolve().parents[1]
    missing_by_module = {
        relative_path: _missing_docstrings(root / relative_path)
        for relative_path in CONTROLLER_DOCSTRING_TARGETS
    }

    missing_by_module = {
        relative_path: missing
        for relative_path, missing in missing_by_module.items()
        if missing
    }

    assert missing_by_module == {}


def test_runtime_modules_define_docstrings_for_functions_and_methods() -> None:
    root = Path(__file__).resolve().parents[1]
    missing_by_module = {
        relative_path: _missing_docstrings(root / relative_path)
        for relative_path in RUNTIME_DOCSTRING_TARGETS
    }

    missing_by_module = {
        relative_path: missing
        for relative_path, missing in missing_by_module.items()
        if missing
    }

    assert missing_by_module == {}


def test_tooling_modules_define_docstrings_for_functions_and_methods() -> None:
    root = Path(__file__).resolve().parents[1]
    missing_by_module = {
        relative_path: _missing_docstrings(root / relative_path)
        for relative_path in TOOLING_DOCSTRING_TARGETS
    }

    missing_by_module = {
        relative_path: missing
        for relative_path, missing in missing_by_module.items()
        if missing
    }

    assert missing_by_module == {}
