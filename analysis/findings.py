"""Print the values that the ContinuumBench paper reports in its Results section.

Each subcommand reads the run directories of one sweep (every ``summary.json`` below
``--sweep-dir``, with the ``provenance/config.yaml`` that the sweep runner stores beside it)
and prints the paper's values for one result, in the paper's terms:

    python analysis/findings.py main        --sweep-dir runs/main
    python analysis/findings.py replan      --sweep-dir runs/replan_ablation
    python analysis/findings.py replan-milp --sweep-dir runs/replan_milp
    python analysis/findings.py load        --sweep-dir runs/load_sweep
    python analysis/findings.py placement   --sweep-dir runs/placement_sweep
    python analysis/findings.py failure     --sweep-dir runs/sensitivity_failure
    python analysis/findings.py stressors   --contact-dir runs/sensitivity_contact \\
                                            --load-dir runs/sensitivity_load

``main`` gives Table 3 and the comparisons around it, ``replan`` and ``replan-milp`` give
Finding 1, ``load`` Finding 2, ``placement`` Finding 3, ``failure`` the first half of Finding 4,
and ``stressors`` the rank changes under contact and load stress. The score robustness check
(the 81-point weight grid) is ``analysis/score_sensitivity.py``.

Controllers are ranked with the completion-aware score and the selection rule of
``analysis/tables.py``: a controller that completes less than 10% of its tasks is ranked below
every other one, and equal scores keep alphabetical order. Means and standard deviations are
over seeds. Decision times are measured on the machine that ran the sweep.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tables import _composite_score as completion_aware_score  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_COMPLETION_FOR_RANK = 0.1

SCENARIOS = ["eo_dual_stream", "driving_v2x", "split_inference", "industrial_iot"]
SCENARIO_NAMES = {
    "eo_dual_stream": "EO dual stream",
    "driving_v2x": "Driving V2X",
    "split_inference": "Split inference",
    "industrial_iot": "Industrial IoT",
}
# The R1 config of each scenario: the 1.0x load of the load sweep.
SCENARIO_CONFIGS = {
    "eo_dual_stream": "configs/eo_small.yaml",
    "driving_v2x": "configs/driving_small.yaml",
    "split_inference": "configs/split_inference_small.yaml",
    "industrial_iot": "configs/industrial_iot_small.yaml",
}
REGIMES = {"T0": "R1", "T4": "R2"}

SCALE_CAPABLE = ["hpa", "keda", "separated_loops", "learned_stub", "rl_meta"]
PLACEMENT_ONLY = ["best_fit", "placement_only:cost_greedy", "placement_only:milp", "round_robin"]
CONTROLLER_NAMES = {
    "hpa": "HPA",
    "keda": "KEDA",
    "separated_loops": "Joint heuristic",
    "learned_stub": "Feature-rule",
    "rl_meta": "Meta-rule",
    "best_fit": "Best-fit",
    "round_robin": "Round-robin",
    "placement_only:cost_greedy": "Cost-greedy",
    "placement_only:milp": "MILP",
    "placement_only:min_latency": "min-latency placement",
    "no_scaling": "over-provisioned reference",
}
STRATEGIES = [
    "min_latency",
    "nearest_feasible_edge",
    "dependency_aware",
    "best_fit",
    "cost_greedy",
    "milp",
]
STRATEGY_NAMES = {
    "min_latency": "min-latency",
    "nearest_feasible_edge": "nearest-feasible-edge",
    "dependency_aware": "dependency-aware",
    "best_fit": "best-fit",
    "cost_greedy": "cost-greedy",
    "milp": "MILP",
}
CONTACT_LEVELS = ["mild", "base", "stress"]


@dataclass
class Run:
    """One run directory: its summary, its rendered config and, if recorded, its sweep entry."""

    path: Path
    summary: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    sweep: dict[str, Any] = field(default_factory=dict)

    def _get(self, section: str, key: str, default: Any = 0.0) -> Any:
        return self.summary.get(section, {}).get(key, default)

    @property
    def scenario(self) -> str:
        return str(self._get("metadata", "scenario", ""))

    @property
    def controller(self) -> str:
        return str(self._get("metadata", "controller", ""))

    @property
    def seed(self) -> int:
        return int(self._get("metadata", "seed", 0))

    @property
    def regime(self) -> str:
        tier = str(self.summary.get("metadata", {}).get("realism", {}).get("detected_tier", ""))
        return REGIMES.get(tier, tier)

    @property
    def completion(self) -> float:
        return float(self._get("totals", "completion_ratio"))

    @property
    def generated(self) -> int:
        return int(self._get("totals", "generated_tasks", 0))

    @property
    def slo_violation(self) -> float:
        """Completion-aware SLO-violation rate (B + U) / (A + B + U)."""
        return float(self._get("qos", "slo_violation_rate"))

    @property
    def late_among_completed(self) -> float:
        """Share of completed tasks that finished after their deadline (U is not counted)."""
        return float(self._get("qos", "deadline_miss_rate"))

    @property
    def on_time(self) -> float:
        """On-time delivery A / G: tasks completed before their deadline, over generated tasks."""
        qos = self.summary.get("qos", {})
        on_time = int(qos.get("slo_eligible_count", 0)) - int(qos.get("slo_violation_count", 0))
        return on_time / self.generated if self.generated else 0.0

    @property
    def migrations(self) -> float:
        return float(self._get("stability", "migrations_total"))

    @property
    def decision_ms(self) -> float:
        """p95 controller decision time per epoch, in milliseconds."""
        return float(self._get("controller_overhead_ms", "p95"))

    @property
    def nodes(self) -> int:
        infra = self.config.get("infrastructure", {})
        return sum(int(infra.get(key, 0)) for key in ("n_space", "n_iot", "n_edge", "n_cloud"))

    @property
    def metrics(self) -> dict[str, float]:
        """The fields of the completion-aware score."""
        return {
            "completion_ratio": self.completion,
            "pending_rate": float(self._get("totals", "pending_rate")),
            "slo_violation_rate": self.slo_violation,
            "latency_p95_s": float(self._get("latency_s", "p95")),
            "migrations_total": self.migrations,
        }


def load_runs(sweep_dir: str | Path) -> list[Run]:
    """Load every run below ``sweep_dir`` (searched recursively)."""

    runs: list[Run] = []
    for summary_path in sorted(Path(sweep_dir).rglob("summary.json")):
        run_dir = summary_path.parent
        if ".sweep_staging" in run_dir.parts:
            continue
        config_path = run_dir / "provenance" / "config.yaml"
        sweep_path = run_dir / "provenance" / "sweep.json"
        config = (
            yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        )
        sweep = json.loads(sweep_path.read_text(encoding="utf-8")) if sweep_path.exists() else {}
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        runs.append(Run(path=run_dir, summary=summary, config=config, sweep=sweep))
    if not runs:
        raise SystemExit(f"No runs found below {sweep_dir}")
    check_complete(runs, sweep_dir)
    return runs


def check_complete(
    runs: list[Run], sweep_dir: str | Path, manifest_dir: Path = REPO_ROOT / "sweeps"
) -> None:
    """Fail on a missing or repeated run, using provenance/sweep.json and the sweep's manifest.

    The expected (run, seed) pairs are the manifest's runs times its seeds, read from
    ``<manifest_dir>/<sweep>.yaml``; without that file, they are the runs and seeds present.
    Runs without a sweep entry, such as those written by ``cli run``, are not checked.
    """

    entries = [
        (str(r.sweep["run"]), int(r.sweep["seed"]))
        for r in runs
        if "run" in r.sweep and "seed" in r.sweep
    ]
    if not entries:
        return
    names = {str(r.sweep.get("sweep", "")) for r in runs if r.sweep}
    if len(names) > 1:
        raise SystemExit(f"{sweep_dir} mixes the sweeps {', '.join(sorted(names))}")
    manifest_path = Path(manifest_dir) / f"{names.pop()}.yaml"
    if manifest_path.exists():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        expected = {
            (str(run["name"]), int(seed)) for run in manifest["runs"] for seed in manifest["seeds"]
        }
    else:
        expected = {(n, seed) for n, _ in entries for _, seed in entries}
    duplicates = sorted({e for e in entries if entries.count(e) > 1})
    missing = sorted(expected - set(entries))
    problems = [f"run {n}, seed {s} appears more than once" for n, s in duplicates]
    problems += [f"run {n}, seed {s} is missing" for n, s in missing]
    if problems:
        raise SystemExit(
            f"Incomplete or repeated sweep in {sweep_dir} (delete it and rerun the sweep): "
            + "; ".join(problems[:5])
            + ("; ..." if len(problems) > 5 else "")
        )


# ---------------------------------------------------------------------------------------------
# Shared helpers


def mean(values: Iterable[float]) -> float:
    return statistics.mean(list(values))


def stdev(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.stdev(values) if len(values) > 1 else 0.0


def group_by(runs: Iterable[Run], key: Callable[[Run], Any]) -> dict[Any, list[Run]]:
    groups: dict[Any, list[Run]] = defaultdict(list)
    for run in runs:
        groups[key(run)].append(run)
    return dict(groups)


def mean_score(runs: list[Run]) -> float:
    """Completion-aware score of a controller from its mean metrics over seeds."""
    fields = runs[0].metrics.keys()
    return completion_aware_score(
        {name: mean(run.metrics[name] for run in runs) for name in fields}
    )


def rank(by_controller: dict[str, list[Run]]) -> list[tuple[str, float]]:
    """Order controllers by the selection rule of analysis/tables.py.

    Returns (controller, score) pairs, best first.
    """
    scored = [
        (controller, mean(r.completion for r in runs) >= MIN_COMPLETION_FOR_RANK, mean_score(runs))
        for controller, runs in sorted(by_controller.items())
    ]
    ordered = sorted(scored, key=lambda item: (item[1], item[2]), reverse=True)
    return [(controller, score) for controller, _, score in ordered]


def select(by_controller: dict[str, list[Run]], family: list[str]) -> tuple[str, list[str]]:
    """Best controller of a family, and the controllers whose score ties with it."""
    ranking = rank({c: runs for c, runs in by_controller.items() if c in family})
    best, best_score = ranking[0]
    ties = [c for c, score in ranking[1:] if abs(score - best_score) < 1e-12]
    return best, ties


def name(controller: str) -> str:
    return CONTROLLER_NAMES.get(controller, controller)


def cell(runs: list[Run]) -> str:
    """Completion / SLO-violation rate / p95 latency, as in Table 3."""
    return (
        f"{mean(r.completion for r in runs):.2f}/{mean(r.slo_violation for r in runs):.2f}/"
        f"{mean(r.metrics['latency_p95_s'] for r in runs):.1f}"
    )


def by_seed(runs: list[Run]) -> dict[int, Run]:
    return {run.seed: run for run in runs}


def arrival_rate(config: dict[str, Any]) -> float:
    """Mean arrival rate of a config (tasks per second, summed over sources)."""
    total = 0.0
    for stream in config.get("workload", {}).get("streams", {}).values():
        segments = stream.get("segments", [])
        epochs = sum(float(s["epochs"]) for s in segments)
        if epochs:
            total += sum(float(s["rate"]) * float(s["epochs"]) for s in segments) / epochs
    return total


# ---------------------------------------------------------------------------------------------
# Table 3 and the main comparison


def report_main(runs: list[Run]) -> None:
    cells = group_by(runs, lambda r: (r.scenario, r.regime))
    print("Table 3: completion / SLO-violation rate / p95 latency (s), means over seeds")
    seed_slo = seed_completion = seed_total = 0
    exceptions: list[str] = []
    hpa_top = 0
    hpa_gap = 0.0
    largest_gain = (0.0, "")
    sds: list[tuple[float, float]] = []
    for scenario in SCENARIOS:
        for regime in ("R1", "R2"):
            if (scenario, regime) not in cells:
                continue
            controllers = group_by(cells[(scenario, regime)], lambda r: r.controller)
            placement, ties = select(controllers, PLACEMENT_ONLY)
            hpa = controllers["hpa"]
            label = name(placement) + (
                f" (tied with {', '.join(name(c) for c in ties)})" if ties else ""
            )
            print(
                f"  {SCENARIO_NAMES[scenario]:<16} {regime}  HPA: {cell(hpa):<16} {label}: "
                f"{cell(controllers[placement])}"
            )

            scale_ranking = rank({c: v for c, v in controllers.items() if c in SCALE_CAPABLE})
            if scale_ranking[0][0] == "hpa":
                hpa_top += 1
            else:
                hpa_gap = max(hpa_gap, scale_ranking[0][1] - mean_score(hpa))

            for group in (hpa, controllers[placement]):
                sds.append(
                    (stdev(r.completion for r in group), stdev(r.slo_violation for r in group))
                )
            gain = mean(r.completion for r in hpa) - mean(
                r.completion for r in controllers[placement]
            )
            if gain > largest_gain[0]:
                largest_gain = (
                    gain,
                    f"{SCENARIO_NAMES[scenario]} {regime}, "
                    f"{mean(r.completion for r in controllers[placement]):.2f} to "
                    f"{mean(r.completion for r in hpa):.2f}",
                )

            placement_seeds = by_seed(controllers[placement])
            for seed, run in sorted(by_seed(hpa).items()):
                other = placement_seeds.get(seed)
                if other is None:
                    continue
                seed_total += 1
                seed_slo += run.slo_violation < other.slo_violation
                if run.completion >= other.completion:
                    seed_completion += 1
                else:
                    exceptions.append(
                        f"{SCENARIO_NAMES[scenario]} {regime} seed {seed} "
                        f"({run.completion:.3f} against {other.completion:.3f})"
                    )

    pairs = len([1 for key in cells if key[1] in ("R1", "R2")])
    print(
        f"\nHPA ranks first among the scale-capable controllers in {hpa_top} of {pairs} pairs; "
        f"elsewhere its score is within {hpa_gap:.4f} of the first."
    )
    print(
        f"Seed by seed, HPA has the lower SLO-violation rate in {seed_slo} of {seed_total} "
        f"comparisons and equal or higher completion in {seed_completion} of {seed_total}"
        + (f" (lower in {'; '.join(exceptions)})." if exceptions else ".")
    )
    print(
        "Largest completion gain of HPA over the placement-only representative: "
        f"{largest_gain[0]:.2f} "
        f"({largest_gain[1]})."
    )
    print(
        "Largest standard deviation over seeds in the table: completion "
        f"{max(s[0] for s in sds):.3f}, "
        f"SLO-violation rate {max(s[1] for s in sds):.3f}."
    )

    iiot = group_by(cells.get(("industrial_iot", "R2"), []), lambda r: r.controller)
    if "hpa" in iiot and "keda" in iiot:
        print(
            "Industrial IoT, R2: "
            + "; ".join(
                f"{name(c)} completes {mean(r.completion for r in iiot[c]):.2f}, "
                f"SLO-violation rate {mean(r.slo_violation for r in iiot[c]):.2f}"
                for c in ("hpa", "keda")
            )
            + "."
        )

    step_ms = 1000.0 * float(runs[0].summary.get("metadata", {}).get("step_seconds", 1.0))
    times = {
        key: mean(r.decision_ms for r in group)
        for key, group in group_by(runs, lambda r: (r.scenario, r.regime, r.controller)).items()
    }
    others = [t for (_, _, c), t in times.items() if c != "placement_only:milp"]
    milp = [t for (_, _, c), t in times.items() if c == "placement_only:milp"]
    print(
        f"p95 decision time per epoch (mean over seeds of each controller, scenario and regime): "
        f"at most {max(others):.1f} ms ({100 * max(others) / step_ms:.2f}% of the {step_ms:.0f} ms "
        f"control period) except the MILP, {min(milp):.1f} to {max(milp):.1f} ms. "
        "Timings depend on the machine."
    )


# ---------------------------------------------------------------------------------------------
# Finding 1: re-planning schedules


def schedule(run: Run) -> str:
    placement = run.config.get("placement", {})
    on_scale = bool(placement.get("replan_on_scale", False))
    periodic = bool(placement.get("periodic_replan", False))
    if on_scale and periodic:
        return "coupled"
    if periodic:
        return "periodic"
    return "fixed" if not on_scale else "on-scale"


def replan_summary(runs: list[Run]) -> dict[str, Any]:
    """Completion differences across re-planning schedules and across node counts."""

    def configuration(run: Run) -> tuple:
        return (
            run.scenario,
            run.regime,
            run.nodes,
            run.config.get("placement", {}).get("strategy"),
            run.controller,
        )

    groups = group_by(runs, configuration)
    schedule_diff = 0.0
    for group in groups.values():
        for same_seed in group_by(group, lambda r: r.seed).values():
            values = [r.completion for r in same_seed]
            schedule_diff = max(schedule_diff, max(values) - min(values))

    node_diff = 0.0
    for group in group_by(
        runs,
        lambda r: (r.scenario, r.regime, configuration(r)[3], r.controller, schedule(r), r.seed),
    ).values():
        values = [r.completion for r in group]
        if len({r.nodes for r in group}) > 1:
            node_diff = max(node_diff, max(values) - min(values))

    largest = max(r.nodes for r in runs)
    hpa_largest = [r.decision_ms for r in runs if r.nodes == largest and r.controller == "hpa"]
    return {
        "runs": len(runs),
        "configurations": len(groups),
        "schedules": sorted({schedule(r) for r in runs}),
        "schedule_difference": schedule_diff,
        "node_count_difference": node_diff,
        "node_counts": sorted({r.nodes for r in runs}),
        "migrations": sum(r.migrations for r in runs),
        "largest_nodes": largest,
        "hpa_decision_ms_largest": mean(hpa_largest) if hpa_largest else float("nan"),
    }


def report_replan(runs: list[Run]) -> None:
    result = replan_summary(runs)
    print(
        f"Re-planning ablation: {result['runs']} runs, {result['configurations']} configurations x "
        f"{len(result['schedules'])} schedules ({', '.join(result['schedules'])})."
    )
    print(
        f"Largest completion difference between schedules, same configuration and seed: "
        f"{result['schedule_difference']:.2e}."
    )
    print(f"Relocations of a placed stage, all runs together: {result['migrations']:.0f}.")
    print(
        f"Node counts {result['node_counts']}: largest completion difference between node counts, "
        f"same scenario, controller, schedule and seed: {result['node_count_difference']:.2e}."
    )
    print(
        f"HPA's p95 decision time on the {result['largest_nodes']}-node infrastructures: "
        f"{result['hpa_decision_ms_largest']:.0f} ms (mean over runs; timings depend on the "
        "machine)."
    )


def report_replan_milp(runs: list[Run]) -> None:
    def capacity(run: Run) -> float:
        return float(run.config.get("infrastructure", {}).get("edge", {}).get("cpu", 0.0))

    arms = group_by(runs, lambda r: "coupled" if schedule(r) == "coupled" else "fixed")
    if set(arms) != {"coupled", "fixed"}:
        raise SystemExit("Expected a coupled and a fixed schedule")
    print(
        "MILP placement re-planned after every scale event and periodically (coupled), against "
        "placing only newly activated workers (fixed); HPA scaling."
    )
    for edge_cpu, group in sorted(group_by(runs, capacity).items()):
        split = group_by(group, schedule)
        fixed_runs, coupled_runs = split.get("fixed", []), split.get("coupled", [])
        if not (fixed_runs and coupled_runs):
            raise SystemExit(
                f"Edge nodes of {edge_cpu:g} cores: expected a coupled and a fixed schedule"
            )
        print(
            f"  edge nodes of {edge_cpu:g} cores: completion fixed "
            f"{mean(r.completion for r in fixed_runs):.4f}, coupled "
            f"{mean(r.completion for r in coupled_runs):.4f}; "
            f"relocations per run fixed {mean(r.migrations for r in fixed_runs):.1f}, "
            f"coupled {mean(r.migrations for r in coupled_runs):.1f}"
        )
    fixed = [r.completion for r in arms["fixed"]]
    coupled = [r.completion for r in arms["coupled"]]
    print(
        f"All {len(coupled)} runs per schedule: completion fixed {mean(fixed):.4f} +- "
        f"{stdev(fixed):.4f}, "
        f"coupled {mean(coupled):.4f} +- {stdev(coupled):.4f}; re-planning adds "
        f"{mean(coupled) - mean(fixed):+.4f}, with "
        f"{mean(r.migrations for r in arms['coupled']):.1f} "
        f"relocations per run (fixed: {mean(r.migrations for r in arms['fixed']):.1f})."
    )


# ---------------------------------------------------------------------------------------------
# Finding 2: load sweep


def load_factor(run: Run, base_rates: dict[str, float]) -> float:
    return round(arrival_rate(run.config) / base_rates[run.scenario], 2)


def base_arrival_rates(scenarios: Iterable[str]) -> dict[str, float]:
    rates = {}
    for scenario in scenarios:
        path = REPO_ROOT / SCENARIO_CONFIGS[scenario]
        rates[scenario] = arrival_rate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return rates


def placement_signature(placement: dict[str, str]) -> list[tuple[str, tuple[str, ...]]]:
    """A mapping up to a relabeling of nodes of the same tier: which stages share each node."""
    stages_by_node: dict[str, list[str]] = defaultdict(list)
    for stage, node in placement.items():
        stages_by_node[node].append(stage)
    return sorted(
        (node.split("_")[0], tuple(sorted(stages))) for node, stages in stages_by_node.items()
    )


def placements(run: Run) -> list[list[tuple[str, tuple[str, ...]]]]:
    path = run.path / "epochs.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [placement_signature(json.loads(line).get("placement", {})) for line in handle]


def load_summary(runs: list[Run]) -> dict[str, Any]:
    rates = base_arrival_rates({r.scenario for r in runs})
    combos = group_by(runs, lambda r: (r.scenario, load_factor(r, rates)))
    scale = ["hpa", "separated_loops", "keda"]
    placement = ["placement_only:min_latency", "placement_only:milp"]
    shares: dict[tuple[str, float, str], float] = {}
    non_saturated = wins = 0
    for (scenario, factor), group in combos.items():
        controllers = group_by(group, lambda r: r.controller)
        reference = mean(r.completion for r in controllers["no_scaling"])
        for controller in scale + placement:
            completion = mean(r.completion for r in controllers[controller])
            shares[(scenario, factor, controller)] = 100 * completion / reference
        placement_runs = [r for c in placement for r in controllers[c]]
        if all(r.completion >= 1.0 for r in placement_runs):
            continue  # saturated: placement-only control already completes every task
        non_saturated += 1
        seeds = sorted({r.seed for r in group})
        wins += all(
            min(by_seed(controllers[c])[s].completion for c in scale)
            > max(by_seed(controllers[c])[s].completion for c in placement)
            for s in seeds
        )
    milp_gap = max(
        abs(
            by_seed(group_by(g, lambda r: r.controller)["placement_only:milp"])[s].completion
            - by_seed(group_by(g, lambda r: r.controller)["placement_only:min_latency"])[
                s
            ].completion
        )
        for g in combos.values()
        for s in {r.seed for r in g}
    )
    return {
        "combinations": len(combos),
        "factors": sorted({f for _, f in combos}),
        "shares": shares,
        "non_saturated": non_saturated,
        "wins": wins,
        "milp_completion_gap": milp_gap,
        "milp_relocations": max(
            r.migrations for r in runs if r.controller == "placement_only:milp"
        ),
        "combos": combos,
    }


def report_load(runs: list[Run], compare_mappings: bool = True) -> None:
    result = load_summary(runs)
    shares = result["shares"]
    factors = result["factors"]
    print(
        f"Load sweep: {len(runs)} runs; completion as % of the over-provisioned reference "
        f"(all workers active from t = 0), loads {', '.join(f'{f:.1f}x' for f in factors)}."
    )
    controllers = [
        "hpa",
        "separated_loops",
        "keda",
        "placement_only:min_latency",
        "placement_only:milp",
    ]
    for scenario in SCENARIOS:
        if not any(key[0] == scenario for key in shares):
            continue
        print(f"  {SCENARIO_NAMES[scenario]}")
        for controller in controllers:
            values = "  ".join(f"{shares[(scenario, f, controller)]:6.1f}" for f in factors)
            print(f"    {name(controller):<24}{values}")
    for label, members in (
        ("HPA", ["hpa"]),
        ("Joint heuristic", ["separated_loops"]),
        ("KEDA", ["keda"]),
        ("Placement-only control (min-latency, MILP)", controllers[3:]),
    ):
        values = [v for (_, _, c), v in shares.items() if c in members]
        print(f"{label} reaches {min(values):.0f}-{max(values):.0f}% of the reference.")
    degrade = [
        SCENARIO_NAMES[s]
        for s in SCENARIOS
        if (s, factors[0], controllers[3]) in shares
        and shares[(s, factors[-1], controllers[3])] < shares[(s, factors[0], controllers[3])]
    ]
    print(
        f"Placement-only control falls further below the reference at {factors[-1]:.1f}x than at "
        f"{factors[0]:.1f}x in: {', '.join(degrade)}."
    )
    print(
        f"Non-saturated combinations (placement-only control misses some task): "
        f"{result['non_saturated']} of {result['combinations']}; in {result['wins']} of them every "
        "scale-capable controller completes more than placement-only control in every seed."
    )
    relocations = result["milp_relocations"]
    print(
        f"MILP against min-latency placement at fixed capacity: largest completion difference "
        f"{result['milp_completion_gap']:.2e}; at most {relocations:.0f} "
        f"relocation{'' if relocations == 1 else 's'} per run."
    )
    if compare_mappings:
        same = total = 0
        for group in result["combos"].values():
            controllers_by = group_by(group, lambda r: r.controller)
            minlat = by_seed(controllers_by["placement_only:min_latency"])
            for seed, run in by_seed(controllers_by["placement_only:milp"]).items():
                a, b = placements(run), placements(minlat[seed])
                same += sum(x == y for x, y in zip(a, b))
                total += min(len(a), len(b))
        if total:
            print(
                f"Their mappings agree, up to a relabeling of identical nodes, in {same} of "
                f"{total} "
                f"epochs ({100 * same / total:.1f}%)."
            )
        else:
            print("Their mappings are not compared: the runs have no epoch logs (epochs.jsonl).")


# ---------------------------------------------------------------------------------------------
# Finding 3: placement sweep


def placement_arm(run: Run) -> str:
    """stress, control or eo: from the sweep entry, the application, or the directory layout."""
    run_name = str(run.sweep.get("run", ""))
    if run_name:
        return run_name.split("_", 1)[0]
    application = str(run.config.get("scenario_config", {}).get("application_id", ""))
    if application:
        lowered = application.lower()
        return "stress" if "stress" in lowered else "control" if "control" in lowered else "eo"
    for part in reversed(run.path.parts):
        if part in ("stress", "control", "eo"):
            return part
    return "eo"


def placement_summary(runs: list[Run]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for arm, group in group_by(runs, placement_arm).items():
        strategies = group_by(group, lambda r: r.controller.split(":")[-1])
        table = {
            strategy: (
                mean(r.completion for r in strategy_runs),
                mean(r.on_time for r in strategy_runs),
                len(strategy_runs),
            )
            for strategy, strategy_runs in strategies.items()
        }
        on_time = [v[1] for v in table.values()]
        deadline = group[0].config.get("stages", {}).get("Sink", {}).get("deadline_s")
        summary[arm] = {"table": table, "spread": max(on_time) - min(on_time), "deadline": deadline}
    return summary


def report_placement(runs: list[Run]) -> None:
    summary = placement_summary(runs)
    order = [arm for arm in ("stress", "control", "eo") if arm in summary] + [
        arm for arm in summary if arm not in ("stress", "control", "eo")
    ]
    labels = {
        "stress": "Placement-stress variant",
        "control": "Placement-stress settings relaxed",
        "eo": "Unmodified EO",
    }
    for arm in order:
        table = summary[arm]["table"]
        deadline = summary[arm]["deadline"]
        print(
            f"{labels.get(arm, arm)}"
            + (f" (Sink deadline {deadline:g} s)" if deadline else "")
            + ": completion, on-time delivery A/G (means over seeds)"
        )
        for strategy in [s for s in STRATEGIES if s in table] + [
            s for s in table if s not in STRATEGIES
        ]:
            completion, on_time, n = table[strategy]
            label = STRATEGY_NAMES.get(strategy, strategy)
            print(f"  {label:<24}{completion:7.3f}{on_time:9.3f}   ({n} seeds)")
        print(f"  on-time delivery spreads the strategies by {summary[arm]['spread']:.2f}")
    if "stress" in summary and "cost_greedy" in summary["stress"]["table"]:
        table = summary["stress"]["table"]
        best_on_time = max(v[1] for v in table.values())
        best = [s for s, v in table.items() if abs(v[1] - best_on_time) < 1e-12]
        greedy_completion, greedy_on_time, _ = table["cost_greedy"]
        ratios = [100 * greedy_completion / table[s][0] for s in best]
        share = (
            f"{min(ratios):.0f}%"
            if round(min(ratios)) == round(max(ratios))
            else f"{min(ratios):.0f}-{max(ratios):.0f}%"
        )
        print(
            f"Placement-stress variant: cost-greedy completes {share} as many "
            f"tasks as the best on-time strateg{'ies' if len(best) > 1 else 'y'} "
            f"({', '.join(STRATEGY_NAMES.get(s, s) for s in best)}), but delivers "
            f"{100 * greedy_on_time:.0f}% on time against {100 * best_on_time:.0f}% "
            f"({best_on_time / greedy_on_time:.1f}x)."
        )


# ---------------------------------------------------------------------------------------------
# Finding 4: node failures


def failure_probability(run: Run) -> float:
    failures = run.config.get("reliability", {}).get("failures", {})
    if str(failures.get("mode", "disabled")) == "disabled":
        return 0.0
    return float(failures.get("node_failure_probability", 0.0))


def failure_summary(runs: list[Run]) -> dict[str, Any]:
    """Configurations whose violation rate falls as node failures rise (the inversion)."""
    rows = []
    for (scenario, controller), group in sorted(
        group_by(runs, lambda r: (r.scenario, r.controller)).items()
    ):
        levels = group_by(group, failure_probability)
        ps = sorted(levels)
        completed_only = [mean(r.late_among_completed for r in levels[p]) for p in ps]
        aware = [mean(r.slo_violation for r in levels[p]) for p in ps]
        rows.append((scenario, controller, ps, completed_only, aware))
    return {
        "rows": rows,
        "completed_only_falls": [r for r in rows if r[3][-1] < r[3][0]],
        "aware_falls": [r for r in rows if r[4][-1] < r[4][0]],
    }


def report_failure(runs: list[Run]) -> None:
    result = failure_summary(runs)
    rows = result["rows"]
    falls = "; ".join(
        f"{SCENARIO_NAMES.get(r[0], r[0])}, {name(r[1])}" for r in result["aware_falls"]
    )
    ps = rows[0][2]
    print(
        f"Node unavailability at p = {', '.join(f'{p:g}' for p in ps)} per node and epoch; "
        "violation rate "
        "over completed tasks only (late / completed) and completion-aware ((B + U) / (A + B + U)):"
    )
    for scenario, controller, _, completed_only, aware in rows:
        print(
            f"  {SCENARIO_NAMES.get(scenario, scenario):<16} {name(controller):<18}"
            f"{'  '.join(f'{v:.3f}' for v in completed_only)}    "
            f"{'  '.join(f'{v:.3f}' for v in aware)}"
        )
    print(
        f"Over completed tasks only, the rate is lower at p = {ps[-1]:g} than without failures in "
        f"{len(result['completed_only_falls'])} of {len(rows)} configurations; counting the "
        "unfinished "
        f"tasks U, in {len(result['aware_falls'])}" + (f" ({falls})." if falls else ".")
    )


# ---------------------------------------------------------------------------------------------
# Stressor rank changes


def contact_level(run: Run) -> str:
    stem = Path(
        str(run.config.get("infrastructure", {}).get("contact_schedule", {}).get("path", ""))
    ).stem
    return "mild" if stem.endswith("_mild") else "stress" if stem.endswith("_stress") else "base"


def rank_changes(
    runs: list[Run], level: Callable[[Run], Any], order: Callable[[Any], Any]
) -> dict[str, Any]:
    """Per scenario: controllers whose score rank differs between the lightest and heaviest "
    "level."""
    changes = {}
    for scenario, group in group_by(runs, lambda r: r.scenario).items():
        by_level = group_by(group, level)
        lightest, heaviest = min(by_level, key=order), max(by_level, key=order)
        ranks = {}
        for key in (lightest, heaviest):
            ranking = rank(group_by(by_level[key], lambda r: r.controller))
            ranks[key] = {
                controller: position for position, (controller, _) in enumerate(ranking, start=1)
            }
        changed = [c for c in ranks[lightest] if ranks[lightest][c] != ranks[heaviest].get(c)]
        changes[scenario] = (len(changed), len(ranks[lightest]), lightest, heaviest)
    return changes


def report_stressors(contact_runs: list[Run] | None, load_stress_runs: list[Run] | None) -> None:
    if contact_runs:
        changes = rank_changes(contact_runs, contact_level, CONTACT_LEVELS.index)
        for scenario in SCENARIOS:
            if scenario in changes:
                k, n, light, heavy = changes[scenario]
                print(
                    f"Contact stress (the {light} against the {heavy} contact trace): "
                    f"{SCENARIO_NAMES[scenario]} changes {k} of {n} ranks."
                )
    if load_stress_runs:
        changes = rank_changes(
            load_stress_runs, lambda r: round(arrival_rate(r.config), 4), lambda x: x
        )
        for scenario in SCENARIOS:
            if scenario in changes:
                k, n, light, heavy = changes[scenario]
                print(
                    f"Load stress ({light:.2f} against {heavy:.2f} arrivals per second): "
                    f"{SCENARIO_NAMES[scenario]} changes {k} of {n} ranks."
                )


# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="finding", required=True)
    for finding, help_text in (
        ("main", "Table 3 and the comparisons around it"),
        ("replan", "Finding 1: re-planning schedules, node counts, decision time"),
        ("replan-milp", "Finding 1: MILP re-planning with relocation"),
        ("load", "Finding 2: load sweep against the over-provisioned reference"),
        ("placement", "Finding 3: placement strategies under a placement bottleneck"),
        ("failure", "Finding 4: violation rates as node failures rise"),
    ):
        command = sub.add_parser(finding, help=help_text)
        command.add_argument(
            "--sweep-dir", required=True, help="Directory holding the sweep's run directories"
        )
    stressors = sub.add_parser("stressors", help="Rank changes under contact and load stress")
    stressors.add_argument(
        "--contact-dir", help="Run directories of sweeps/sensitivity_contact.yaml"
    )
    stressors.add_argument("--load-dir", help="Run directories of sweeps/sensitivity_load.yaml")
    args = parser.parse_args(argv)

    if args.finding == "stressors":
        if not (args.contact_dir or args.load_dir):
            parser.error("stressors needs --contact-dir, --load-dir or both")
        report_stressors(
            load_runs(args.contact_dir) if args.contact_dir else None,
            load_runs(args.load_dir) if args.load_dir else None,
        )
        return 0
    reports = {
        "main": report_main,
        "replan": report_replan,
        "replan-milp": report_replan_milp,
        "load": report_load,
        "placement": report_placement,
        "failure": report_failure,
    }
    reports[args.finding](load_runs(args.sweep_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
