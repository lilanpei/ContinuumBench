from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ECLYPSE_INSTALL_HINT = (
    "Eclypse is required to run ContinuumBench. Install the eclypse package in "
    "the active environment or set ECLYPSE_HOME to a local Eclypse checkout, "
    "for example: export ECLYPSE_HOME=/path/to/eclypse"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for benchmark entry points."""

    parser = argparse.ArgumentParser(description="ContinuumBench benchmark CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a benchmark scenario")
    run_parser.add_argument("--config", required=True, help="Path to benchmark YAML config")
    run_parser.add_argument(
        "--controller",
        required=True,
        help=(
            "Controller name (separated_loops, static, round_robin, best_fit, "
            "all_local, all_edge, all_cloud, no_scaling, "
            "hpa, keda, learned_stub, rl_meta, placement_only:<strategy>, "
            "anticipatory, constant[:N or :Pool=N,...])"
        ),
    )
    run_parser.add_argument("--seed", type=int, default=0, help="Deterministic run seed")
    run_parser.add_argument("--out", default="runs", help="Output root directory")

    sum_parser = subparsers.add_parser("summarize", help="Summarize a previous run")
    sum_parser.add_argument("--run_dir", required=True, help="Directory containing epochs.jsonl")

    sweep_parser = subparsers.add_parser("sweep", help="Run a manifest-defined batch sweep")
    sweep_parser.add_argument("--manifest", required=True, help="Path to sweep manifest YAML")
    sweep_parser.add_argument("--out", default="runs", help="Output root directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            runner = globals().get("run_benchmark")
            if runner is None:
                from continuum_bench.runner import run_benchmark as runner

            result = runner(
                config_path=Path(args.config),
                controller_name=args.controller,
                seed=int(args.seed),
                out_root=Path(args.out),
            )
            run_dir = Path(result["run_dir"])
            print(f"Run complete: {run_dir}")
            print(f"Epoch logs: {run_dir / 'epochs.jsonl'}")
            print(f"Summary: {run_dir / 'summary.json'}")
            return 0

        if args.command == "summarize":
            summarizer = globals().get("summarize_run")
            if summarizer is None:
                from continuum_bench.runner import summarize_run as summarizer

            summary = summarizer(Path(args.run_dir))
            summary_path = Path(args.run_dir) / "summary.json"
            print(f"Summary written: {summary_path}")
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        if args.command == "sweep":
            sweep_runner = globals().get("run_sweep")
            if sweep_runner is None:
                from continuum_bench.sweeps.runner import run_sweep as sweep_runner

            results = sweep_runner(
                manifest_path=Path(args.manifest),
                out_root=Path(args.out),
            )
            print(f"Sweep complete: {len(results)} runs")
            for result in results:
                print(f"Run complete: {result['run_dir']}")
            return 0

        parser.error(f"Unsupported command: {args.command}")
        return 2
    except ModuleNotFoundError as exc:
        message = str(exc)
        if (exc.name and exc.name.startswith("eclypse")) or "Eclypse is required" in message:
            print(f"Error: {ECLYPSE_INSTALL_HINT}", file=sys.stderr)
            return 1
        raise
    except Exception as exc:  # pragma: no cover - exercised by CLI behavior
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
