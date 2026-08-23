from pathlib import Path


def test_repo_layout_is_coherent() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "pyproject.toml").exists()
    assert (root / "README.md").exists()
    assert (root / "LICENSE").exists()
    assert (root / "pytest.ini").exists()
    assert (root / "src").is_dir()


def test_major_directories_include_readme_guardrails() -> None:
    root = Path(__file__).resolve().parents[1]

    reader_entrypoints = [
        "src/README.md",
        "src/continuum_bench/README.md",
    ]
    top_level_dirs = [
        "analysis",
        "calibration",
        "configs",
        "docs",
        "splits",
        "sweeps",
        "tests",
    ]
    package_dirs = [
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

    missing = [str(root / path) for path in reader_entrypoints if not (root / path).exists()]
    missing.extend(
        str(root / directory / "README.md")
        for directory in top_level_dirs
        if not (root / directory / "README.md").exists()
    )
    missing.extend(
        str(root / "src" / "continuum_bench" / directory / "README.md")
        for directory in package_dirs
        if not (root / "src" / "continuum_bench" / directory / "README.md").exists()
    )

    assert not missing, f"Missing README coverage: {missing}"
