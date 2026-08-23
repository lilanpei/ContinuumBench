from pathlib import Path


def test_readme_mentions_public_benchmark_entrypoints_and_scenarios() -> None:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    contents = readme.read_text(encoding="utf-8")

    assert "python -m continuum_bench.cli run" in contents
    assert "eo_dual_stream" in contents
    assert "driving_v2x" in contents
    assert "contact_open" in contents
    assert "evaluation_regime" in contents
    assert "realism_tier" in contents
    assert "decision_trajectory.jsonl" in contents


def test_root_and_src_readmes_route_new_readers_to_the_next_docs() -> None:
    root = Path(__file__).resolve().parents[1]

    root_contents = (root / "README.md").read_text(encoding="utf-8")
    for path in [
        "src/README.md",
        "src/continuum_bench/README.md",
        "configs/README.md",
        "sweeps/README.md",
        "calibration/README.md",
        "docs/README.md",
        "splits/README.md",
        "analysis/README.md",
        "tests/README.md",
    ]:
        assert f"`{path}`" in root_contents

    src_contents = (root / "src" / "README.md").read_text(encoding="utf-8")
    assert "`src/continuum_bench/`" in src_contents
    assert "`continuum_bench/README.md`" in src_contents


def test_package_readme_maps_major_subsystems_for_new_developers() -> None:
    package_readme = Path(__file__).resolve().parents[1] / "src" / "continuum_bench" / "README.md"
    contents = package_readme.read_text(encoding="utf-8")

    required_subsystems = [
        "controllers",
        "scenarios",
        "placement",
        "oracles",
        "calibration",
        "infra",
        "metrics",
        "runtime",
        "sweeps",
        "workload",
    ]

    for subsystem in required_subsystems:
        assert f"`{subsystem}/`" in contents


def test_targeted_directory_readmes_use_minimum_reader_sections() -> None:
    root = Path(__file__).resolve().parents[1]
    readmes = [
        "analysis/README.md",
        "calibration/README.md",
        "configs/README.md",
        "docs/README.md",
        "splits/README.md",
        "sweeps/README.md",
        "tests/README.md",
        "src/continuum_bench/calibration/README.md",
        "src/continuum_bench/infra/README.md",
        "src/continuum_bench/metrics/README.md",
        "src/continuum_bench/runtime/README.md",
        "src/continuum_bench/sweeps/README.md",
        "src/continuum_bench/workload/README.md",
    ]
    required_sections = [
        "Purpose",
        "Key Files",
        "When To Read",
    ]

    for relative_path in readmes:
        contents = (root / relative_path).read_text(encoding="utf-8")
        for expected in required_sections:
            assert expected in contents, f"{relative_path} missing section: {expected}"


def test_calibration_and_config_docs_describe_current_trace_runtime_semantics() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        "calibration/README.md",
        "configs/README.md",
        "src/continuum_bench/calibration/README.md",
    ]

    for relative_path in paths:
        contents = (root / relative_path).read_text(encoding="utf-8")
        assert "contact_open" in contents, f"{relative_path} missing contact_open semantics"
        assert "edge availability" in contents, f"{relative_path} missing edge availability boundary"

    package_calibration = (
        root / "src" / "continuum_bench" / "calibration" / "README.md"
    ).read_text(encoding="utf-8")
    assert "energy_profiles.yaml" in package_calibration
    assert "contact_traces/" in package_calibration
