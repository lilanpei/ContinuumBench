"""Generate publication figures from ContinuumBench analysis outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


SCENARIOS = ["eo_dual_stream", "driving_v2x", "split_inference", "industrial_iot"]
CONTACT_SCENARIOS = ["eo_dual_stream", "driving_v2x"]
FAMILY_ORDER = ["Placement-only", "Autoscaling", "Joint heuristic", "Rule-based controller baselines"]
CONVENTIONAL_SCALE_CONTROLLERS = {"hpa", "keda", "separated_loops"}
SCENARIO_LABELS = {
    "eo_dual_stream": "EO dual stream",
    "driving_v2x": "Driving V2X",
    "split_inference": "Split inference",
    "industrial_iot": "Industrial IoT",
}
CONTROLLER_LABELS = {
    "separated_loops": "Joint",
    "hpa": "HPA",
    "keda": "KEDA",
    "best_fit": "Best-fit",
    "round_robin": "Round-robin",
    "placement_only:cost_greedy": "Cost-greedy",
    "placement_only:milp": "MILP",
    "learned_stub": "Feature-rule",
    "rl_meta": "Meta-rule",
}
CALLOUT_LABELS = {
    "separated_loops": "Joint",
    "hpa": "HPA",
    "keda": "KEDA",
    "best_fit": "Best-fit",
    "round_robin": "Round-robin",
    "placement_only:cost_greedy": "Cost-greedy",
    "placement_only:milp": "MILP",
    "learned_stub": "Feature\nrule",
    "rl_meta": "Meta\nrule",
}
CONTROLLER_FAMILIES = {
    "best_fit": "Placement-only",
    "round_robin": "Placement-only",
    "placement_only:cost_greedy": "Placement-only",
    "placement_only:milp": "Placement-only",
    "hpa": "Autoscaling",
    "keda": "Autoscaling",
    "separated_loops": "Joint heuristic",
    "learned_stub": "Rule-based controller baselines",
    "rl_meta": "Rule-based controller baselines",
}
FAMILY_COLORS = {
    "Placement-only": "#7f7f7f",
    "Autoscaling": "#1f77b4",
    "Joint heuristic": "#ff7f0e",
    "Rule-based controller baselines": "#d62728",
}
FAMILY_MARKERS = {
    "Placement-only": "^",
    "Autoscaling": "o",
    "Joint heuristic": "s",
    "Rule-based controller baselines": "D",
}
CONTACT_ORDER = {"mild": 0, "base": 1, "stress": 2}


def main(argv: list[str] | None = None) -> int:
    """Run the publication-figure command-line entry point."""

    parser = argparse.ArgumentParser(
        description="Generate ContinuumBench publication figures from saved analysis outputs."
    )
    parser.add_argument(
        "--main",
        default="runs/main/analysis/grouped_strict.csv",
        help="Grouped strict CSV for the main controller comparison.",
    )
    parser.add_argument(
        "--main-aggregate",
        default="runs/main/analysis/aggregate.csv",
        help="Aggregate CSV for selecting per-run trajectory logs.",
    )
    parser.add_argument(
        "--load",
        default="runs/sensitivity_load/analysis/grouped_strict.csv",
        help="Grouped strict CSV for load sensitivity.",
    )
    parser.add_argument(
        "--contact",
        default="runs/sensitivity_contact/analysis/grouped_strict.csv",
        help="Grouped strict CSV for contact sensitivity.",
    )
    parser.add_argument(
        "--delay",
        default="runs/delay_breakdown/analysis/grouped_strict.csv",
        help="Grouped strict CSV for delay-breakdown analysis.",
    )
    parser.add_argument(
        "--rank-stability",
        default="runs/realism_ladder/analysis/rank_stability.csv",
        help="Rank-stability CSV from the regime-ladder analysis.",
    )
    parser.add_argument(
        "--rankings",
        default="runs/realism_ladder/analysis/rankings.csv",
        help="Ranking CSV from the regime-ladder analysis.",
    )
    parser.add_argument(
        "--regime-grouped",
        default="runs/realism_ladder/analysis/grouped_strict.csv",
        help="Grouped strict CSV from the regime-ladder analysis.",
    )
    parser.add_argument(
        "--out",
        default="figs",
        help="Output directory for rendered PDF figures.",
    )
    args = parser.parse_args(argv)

    configure_matplotlib()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "main_tradeoff": plot_main_tradeoff(Path(args.main), out_dir),
        "control_dimension": plot_control_dimension_ablation(Path(args.main), out_dir),
        "controller_overhead": plot_controller_overhead(Path(args.main), out_dir),
        "delay_breakdown": plot_delay_breakdown(Path(args.delay), out_dir),
        "load_stress": plot_load_stress(Path(args.load), out_dir),
        "contact_stress": plot_contact_stress(Path(args.contact), out_dir),
        "rank_stability": plot_rank_stability(
            Path(args.rank_stability),
            Path(args.rankings),
            Path(args.regime_grouped),
            Path(args.contact),
            Path(args.load),
            out_dir,
        ),
        "eo_trajectory": plot_eo_trajectory(Path(args.main_aggregate), out_dir),
    }
    for name, path in outputs.items():
        print(f"{name}: {path}")
    return 0


def configure_matplotlib() -> None:
    """Use compact Matplotlib defaults for publication figures."""

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.titlesize": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "figure.dpi": 180,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def plot_main_tradeoff(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot F2 completion/SLO tradeoffs across controller families."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "main_f2_controller_tradeoff.pdf"
    fig, axes = plt.subplots(2, 2, figsize=(6.7, 4.35), sharex=True, sharey=True)
    label_specs = {
        "eo_dual_stream": {
            "hpa": {"xytext": (30, -12), "ha": "left", "va": "top"},
            "best_fit": {"xytext": (28, 18), "ha": "left", "va": "bottom"},
        },
        "driving_v2x": {
            "hpa": {"xytext": (24, 16), "ha": "left", "va": "bottom"},
            "placement_only:cost_greedy": {
                "xytext": (-34, 20),
                "ha": "right",
                "va": "bottom",
            },
        },
        "split_inference": {
            "hpa": {"xytext": (-18, 24), "ha": "right", "va": "bottom"},
            "best_fit": {"xytext": (28, 18), "ha": "left", "va": "bottom"},
        },
        "industrial_iot": {
            "hpa": {"xytext": (-22, 26), "ha": "right", "va": "bottom"},
            "placement_only:cost_greedy": {
                "xytext": (-36, -18),
                "ha": "right",
                "va": "top",
            },
        },
    }

    for ax, scenario in zip(axes.flat, SCENARIOS):
        scenario_rows = [
            row
            for row in rows
            if row["scenario"] == scenario and row["evaluation_regime"] == "F2"
        ]
        for row in scenario_rows:
            family = CONTROLLER_FAMILIES[row["controller"]]
            marker_size = 23 + min(as_float(row, "latency_p95_s"), 150.0) * 0.2
            alpha = 0.75 if row["valid_for_latency_rank"] == "True" else 0.28
            ax.scatter(
                as_float(row, "completion_ratio"),
                as_float(row, "slo_violation_rate"),
                s=marker_size,
                marker=FAMILY_MARKERS[family],
                color=FAMILY_COLORS[family],
                edgecolor="white",
                linewidth=0.5,
                alpha=alpha,
                zorder=3,
            )

        label_rows = _main_tradeoff_label_rows(scenario_rows)
        for row in label_rows:
            family = CONTROLLER_FAMILIES[row["controller"]]
            ax.scatter(
                as_float(row, "completion_ratio"),
                as_float(row, "slo_violation_rate"),
                s=78,
                marker=FAMILY_MARKERS[family],
                facecolors="none",
                edgecolors="#222222",
                linewidth=0.75,
                zorder=4,
            )
        for row in label_rows:
            spec = label_specs.get(scenario, {}).get(
                row["controller"],
                {"xytext": (8, 8), "ha": "left", "va": "bottom"},
            )
            ax.annotate(
                CALLOUT_LABELS[row["controller"]],
                (as_float(row, "completion_ratio"), as_float(row, "slo_violation_rate")),
                xytext=spec["xytext"],
                textcoords="offset points",
                ha=spec["ha"],
                va=spec["va"],
                fontsize=6.5,
                bbox={
                    "boxstyle": "round,pad=0.18",
                    "facecolor": "white",
                    "edgecolor": "#d9d9d9",
                    "linewidth": 0.4,
                    "alpha": 0.92,
                },
                arrowprops={
                    "arrowstyle": "-",
                    "color": "#555555",
                    "linewidth": 0.55,
                    "shrinkA": 2,
                    "shrinkB": 2,
                },
                zorder=5,
                clip_on=False,
            )

        ax.text(
            0.03,
            0.94,
            SCENARIO_LABELS[scenario],
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=7,
            fontweight="semibold",
            bbox={
                "boxstyle": "round,pad=0.16",
                "facecolor": "white",
                "edgecolor": "#e0e0e0",
                "linewidth": 0.35,
                "alpha": 0.9,
            },
        )
        ax.grid(True, color="#e6e6e6", linewidth=0.6, zorder=0)
        ax.set_xlim(-0.03, 1.05)
        ax.set_ylim(-0.04, 1.05)
        ax.axhline(0, color="#cccccc", linewidth=0.5)
        ax.axvline(1, color="#cccccc", linewidth=0.5)

    for ax in axes[:, 0]:
        ax.set_ylabel("SLO violation rate")
    for ax in axes[-1, :]:
        ax.set_xlabel("Completion ratio")

    legend_handles = [
        Line2D(
            [0],
            [0],
            marker=FAMILY_MARKERS[family],
            color="w",
            markerfacecolor=FAMILY_COLORS[family],
            markeredgecolor="white",
            label=family,
            markersize=6,
        )
        for family in FAMILY_ORDER
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        ncol=4,
        frameon=False,
        bbox_to_anchor=(0.5, 1.015),
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def plot_control_dimension_ablation(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot F2 placement-only, autoscaling, and joint-heuristic metrics."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "control_dimension_ablation.pdf"
    summaries = _control_dimension_summaries(rows)
    xs = list(range(len(summaries)))
    width = 0.24
    scenario_labels = ["EO", "Driving", "Split", "Industrial"]

    fig, axes = plt.subplots(1, 2, figsize=(6.7, 2.65), sharex=True)
    bars = [
        ("Placement-only", -width, "#7f7f7f"),
        ("Autoscaling", 0.0, "#1f77b4"),
        ("Joint heuristic", width, "#ff7f0e"),
    ]
    metric_specs = [
        ("completion_ratio", "Completion ratio"),
        ("slo_violation_rate", "SLO violation rate"),
    ]

    for ax, (metric, ylabel) in zip(axes, metric_specs):
        for family, offset, color in bars:
            values = [summary[family][metric] for summary in summaries]
            ax.bar(
                [x + offset for x in xs],
                values,
                width=width,
                color=color,
                label=family,
                zorder=2,
            )
            for x, value in zip(xs, values):
                if value >= 0.9:
                    text_y = value - 0.055
                    text_color = "white"
                    va = "top"
                else:
                    text_y = value + 0.025
                    text_color = "#222222"
                    va = "bottom"
                ax.text(
                    x + offset,
                    text_y,
                    f"{value:.2f}",
                    ha="center",
                    va=va,
                    fontsize=4.5,
                    color=text_color,
                )
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#e8e8e8", linewidth=0.6, zorder=0)
        ax.set_xticks(xs)
        ax.set_xticklabels(scenario_labels)
        ax.set_ylim(0, 1.08)

    axes[0].legend(frameon=False, loc="upper left", ncol=1, bbox_to_anchor=(0.0, 1.03))
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _control_dimension_summaries(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Return best-family F2 metrics and gains relative to placement-only."""

    summaries: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        scenario_rows = [
            row
            for row in rows
            if row["scenario"] == scenario and row["evaluation_regime"] == "F2"
        ]
        best_rows = {
            family: _best_family_row(scenario_rows, family)
            for family in FAMILY_ORDER
        }
        placement = best_rows["Placement-only"]
        summary: dict[str, Any] = {
            "scenario": scenario,
            "label": SCENARIO_LABELS[scenario],
            "Placement-only": {
                "controller": placement["controller"],
                "completion_ratio": as_float(placement, "completion_ratio"),
                "slo_violation_rate": as_float(placement, "slo_violation_rate"),
            },
        }
        for family in ["Autoscaling", "Joint heuristic", "Rule-based controller baselines"]:
            row = best_rows[family]
            summary[family] = {
                "controller": row["controller"],
                "completion_ratio": as_float(row, "completion_ratio"),
                "slo_violation_rate": as_float(row, "slo_violation_rate"),
                "completion_gain": as_float(row, "completion_ratio")
                - as_float(placement, "completion_ratio"),
                "slo_reduction": as_float(placement, "slo_violation_rate")
                - as_float(row, "slo_violation_rate"),
            }
        summaries.append(summary)
    return summaries


def _best_family_row(
    rows: list[dict[str, str]], family: str
) -> dict[str, str]:
    """Return the highest-score row for a controller family."""

    candidates = [
        row
        for row in rows
        if CONTROLLER_FAMILIES[row["controller"]] == family
        and (family != "Placement-only" or row["valid_for_latency_rank"] == "True")
    ]
    if not candidates:
        raise ValueError(f"No candidate rows for family {family}")
    return max(candidates, key=lambda row: as_float(row, "composite_score"))


def plot_controller_overhead(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot controller decision overhead in the main F2 comparison."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "controller_overhead.pdf"
    summaries = _controller_overhead_summaries(rows)
    xs = list(range(len(FAMILY_ORDER)))
    width = 0.18

    fig, ax = plt.subplots(figsize=(6.7, 2.45))
    scenario_offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]
    scenario_colors = {
        "eo_dual_stream": "#1f77b4",
        "driving_v2x": "#2ca02c",
        "split_inference": "#ff7f0e",
        "industrial_iot": "#9467bd",
    }

    for scenario, offset in zip(SCENARIOS, scenario_offsets):
        values = [summaries[scenario][family]["mean"] for family in FAMILY_ORDER]
        ax.bar(
            [x + offset for x in xs],
            values,
            width=width,
            color=scenario_colors[scenario],
            label=_short_scenario_label(scenario),
            linewidth=0.0,
            zorder=2,
        )

    ax.set_xticks(xs)
    ax.set_xticklabels(["Place", "Scale", "Joint", "Adaptive"])
    ax.set_ylabel("p95 decision time (ms)")
    ax.grid(axis="y", color="#e8e8e8", linewidth=0.6, zorder=0)
    ax.legend(frameon=False, loc="upper left", ncol=4, bbox_to_anchor=(0.0, 1.05))
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _controller_overhead_summaries(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    """Return F2 controller-overhead summaries by scenario and controller family."""

    summaries: dict[str, dict[str, dict[str, float]]] = {}
    for scenario in SCENARIOS:
        summaries[scenario] = {}
        scenario_rows = [
            row
            for row in rows
            if row["scenario"] == scenario and row["evaluation_regime"] == "F2"
        ]
        for family in FAMILY_ORDER:
            family_rows = [
                row
                for row in scenario_rows
                if CONTROLLER_FAMILIES[row["controller"]] == family
            ]
            values = [as_float(row, "controller_overhead_p95_ms") for row in family_rows]
            summaries[scenario][family] = {
                "mean": sum(values) / len(values) if values else 0.0,
                "std": _sample_std(values),
            }
    return summaries


def _sample_std(values: list[float]) -> float:
    """Return sample standard deviation for compact figure error bars."""

    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _short_scenario_label(scenario: str) -> str:
    """Return compact labels for dense multi-scenario figures."""

    return {
        "eo_dual_stream": "EO",
        "driving_v2x": "Driving",
        "split_inference": "Split",
        "industrial_iot": "Industrial",
    }[scenario]


def plot_delay_breakdown(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot task-level delay attribution for selected F2 runs."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "delay_breakdown.pdf"
    summaries = _delay_breakdown_summaries(rows)
    components = [
        ("queue_wait", "Queue", "#4c78a8"),
        ("processing", "Processing", "#f58518"),
        ("transport", "Transport", "#54a24b"),
        ("buffer_wait", "Buffer", "#b279a2"),
    ]
    xs = list(range(len(SCENARIOS)))

    fig, ax = plt.subplots(figsize=(6.7, 2.65))
    bottoms = [0.0 for _ in xs]
    for key, label, color in components:
        values = [summaries[scenario][key] for scenario in SCENARIOS]
        ax.bar(
            xs,
            values,
            bottom=bottoms,
            color=color,
            label=label,
            width=0.62,
            zorder=2,
        )
        bottoms = [base + value for base, value in zip(bottoms, values)]

    for idx, scenario in enumerate(SCENARIOS):
        startup = summaries[scenario]["startup"]
        migration = summaries[scenario]["migration"]
        if startup <= 0.0 and migration <= 0.0:
            continue
        ax.scatter(
            [idx],
            [bottoms[idx] + startup + migration],
            marker="_",
            color="#222222",
            s=50,
            linewidth=1.2,
            zorder=4,
        )

    ax.set_xticks(xs)
    ax.set_xticklabels([_short_scenario_label(scenario) for scenario in SCENARIOS])
    ax.set_ylabel("Mean completed-task delay (s)")
    ax.grid(axis="y", color="#e8e8e8", linewidth=0.6, zorder=0)
    ax.legend(frameon=False, loc="upper left", ncol=4, bbox_to_anchor=(0.0, 1.08))
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _delay_breakdown_summaries(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    """Return selected F2 delay components by scenario."""

    summaries: dict[str, dict[str, float]] = {}
    for scenario in SCENARIOS:
        scenario_rows = [
            row
            for row in rows
            if row["scenario"] == scenario and row["evaluation_regime"] == "F2"
        ]
        if not scenario_rows:
            raise ValueError(f"No F2 delay rows for {scenario}")
        selected = _selected_delay_row(scenario_rows)
        summaries[scenario] = {
            "controller": selected["controller"],
            "queue_wait": as_float(selected, "delay_queue_wait_mean_s"),
            "processing": as_float(selected, "delay_processing_mean_s"),
            "transport": as_float(selected, "delay_transport_mean_s"),
            "buffer_wait": as_float(selected, "delay_buffer_wait_mean_s"),
            "startup": as_float(selected, "delay_startup_mean_s"),
            "migration": as_float(selected, "delay_migration_mean_s"),
        }
    return summaries


def _selected_delay_row(rows: list[dict[str, str]]) -> dict[str, str]:
    """Select a scale-capable row for delay attribution."""

    preferred = [row for row in rows if row["controller"] == "hpa"]
    if preferred:
        return preferred[0]
    scale_capable = [
        row
        for row in rows
        if CONTROLLER_FAMILIES.get(row["controller"]) != "Placement-only"
    ]
    return max(scale_capable or rows, key=lambda row: as_float(row, "composite_score"))


def plot_load_stress(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot HPA load sensitivity for completion and SLO violation."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "load_stress_hpa.pdf"
    fig, axes = plt.subplots(2, 4, figsize=(6.9, 3.25), sharex=False)

    for col, scenario in enumerate(SCENARIOS):
        hpa_rows = sorted(
            [row for row in rows if row["scenario"] == scenario and row["controller"] == "hpa"],
            key=lambda row: as_float(row, "workload_arrival_rate_mean"),
        )
        xs = list(range(len(hpa_rows)))
        load_labels = ["low", "base", "high"]

        completion = [as_float(row, "completion_ratio") for row in hpa_rows]
        completion_std = [as_float(row, "completion_ratio_std") for row in hpa_rows]
        slo = [as_float(row, "slo_violation_rate") for row in hpa_rows]
        slo_std = [as_float(row, "slo_violation_rate_std") for row in hpa_rows]

        ax = axes[0, col]
        ax.errorbar(
            xs,
            completion,
            yerr=completion_std,
            marker="o",
            color="#1f77b4",
            linewidth=1.5,
            capsize=2.5,
        )
        ax.text(
            0.5,
            1.04,
            SCENARIO_LABELS[scenario],
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=6.6,
            fontweight="semibold",
            clip_on=False,
        )
        ax.set_ylim(-0.03, 1.05)
        ax.grid(True, color="#e8e8e8", linewidth=0.6)
        ax.set_xticks(xs)
        ax.set_xticklabels([])
        if col == 0:
            ax.set_ylabel("Completion")

        ax = axes[1, col]
        ax.errorbar(
            xs,
            slo,
            yerr=slo_std,
            marker="s",
            color="#d62728",
            linewidth=1.5,
            capsize=2.5,
        )
        ax.set_ylim(-0.03, 1.05)
        ax.grid(True, color="#e8e8e8", linewidth=0.6)
        ax.set_xticks(xs)
        ax.set_xticklabels(load_labels)
        ax.set_xlabel("Load")
        if col == 0:
            ax.set_ylabel("SLO violation")

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def plot_contact_stress(grouped_csv: Path, out_dir: Path) -> Path:
    """Plot HPA contact sensitivity for workflow-visible intermittent links."""

    rows = read_csv(grouped_csv)
    output_path = out_dir / "contact_stress_hpa.pdf"
    fig, axes = plt.subplots(3, 2, figsize=(6.7, 4.45), sharex=False)
    metrics = [
        ("active_link_ratio_mean", "Active ratio", "#2ca02c"),
        ("buffered_items_peak", "Peak buffer", "#9467bd"),
        ("latency_p95_s", "p95 latency (s)", "#ff7f0e"),
    ]

    for col, scenario in enumerate(CONTACT_SCENARIOS):
        hpa_rows = sorted(
            [row for row in rows if row["scenario"] == scenario and row["controller"] == "hpa"],
            key=lambda row: CONTACT_ORDER[row["contact_profile"]],
        )
        xs = list(range(len(hpa_rows)))
        contact_labels = [row["contact_profile"] for row in hpa_rows]
        for metric_idx, (field, ylabel, color) in enumerate(metrics):
            ax = axes[metric_idx, col]
            values = [as_float(row, field) for row in hpa_rows]
            std_field = f"{field}_std"
            std_values = [
                as_float(row, std_field) if row.get(std_field, "") != "" else 0.0
                for row in hpa_rows
            ]
            ax.errorbar(
                xs,
                values,
                yerr=std_values,
                marker="o",
                color=color,
                linewidth=1.5,
                capsize=2.5,
            )
            if metric_idx == 0:
                ax.text(
                    0.5,
                    1.04,
                    SCENARIO_LABELS[scenario],
                    transform=ax.transAxes,
                    ha="center",
                    va="bottom",
                    fontsize=6.8,
                    fontweight="semibold",
                    clip_on=False,
                )
                ax.set_ylim(0, max(0.65, max(values) * 1.15))
            if metric_idx < 2:
                ax.set_xticks(xs)
                ax.set_xticklabels([])
            else:
                ax.set_xticks(xs)
                ax.set_xticklabels(contact_labels, rotation=20)
                ax.set_xlabel("Contact")
            if col == 0:
                ax.set_ylabel(ylabel, labelpad=8)
            ax.grid(True, color="#e8e8e8", linewidth=0.6)

    fig.tight_layout(rect=[0.025, 0, 1, 1])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def plot_rank_stability(
    rank_stability_csv: Path,
    rankings_csv: Path,
    grouped_csv: Path,
    contact_grouped_csv: Path,
    load_grouped_csv: Path,
    out_dir: Path,
) -> Path:
    """Plot rank changes and metric shifts across designed stress endpoints."""

    del rank_stability_csv, rankings_csv, grouped_csv
    contact_rows = read_csv(contact_grouped_csv)
    load_rows = read_csv(load_grouped_csv)
    output_path = out_dir / "rank_stability.pdf"
    summaries = _stress_rank_summaries(contact_rows, load_rows)

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(7.35, 3.15),
        gridspec_kw={"width_ratios": [1.2, 1.55]},
    )
    ax = axes[0]
    changed_values = [int(summary["changed"]) for summary in summaries]
    stable_values = [int(summary["stable"]) for summary in summaries]
    ys = list(range(len(summaries)))
    total = max(summary["total"] for summary in summaries)
    ax.barh(
        ys,
        stable_values,
        color="#bdbdbd",
        height=0.56,
        label="Stable rank",
        zorder=2,
    )
    ax.barh(
        ys,
        changed_values,
        left=stable_values,
        color="#d62728",
        height=0.56,
        label="Rank changed",
        zorder=2,
    )
    for idx, (stable, changed) in enumerate(zip(stable_values, changed_values)):
        ax.text(
            stable + changed + 0.08,
            idx,
            f"{changed}/{total}",
            ha="left",
            va="center",
            fontsize=7,
        )
    ax.set_yticks(ys)
    ax.set_yticklabels([summary["label"] for summary in summaries])
    ax.set_xlabel("Controllers in sensitivity subset")
    ax.set_xlim(0, total + 1.15)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#e8e8e8", linewidth=0.6, zorder=0)
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.03), ncol=2)

    ax = axes[1]
    metric_labels, shift_labels, shift_values = _stress_metric_shift_matrix(
        contact_rows, load_rows
    )
    normalized = _column_normalized_signed(shift_values)
    image = ax.imshow(normalized, cmap="coolwarm", vmin=-1.0, vmax=1.0, aspect="auto")
    del image
    for row_idx, values in enumerate(shift_values):
        for col_idx, value in enumerate(values):
            if math.isnan(value):
                label = "n/a"
            elif metric_labels[col_idx] in {"p95 (s)", "Buffer"}:
                label = f"{value:+.1f}"
            else:
                label = f"{value:+.2f}"
            ax.text(col_idx, row_idx, label, ha="center", va="center", fontsize=6.5)
    ax.set_xticks(range(len(metric_labels)))
    ax.set_xticklabels(metric_labels)
    ax.set_yticks(range(len(shift_labels)))
    ax.set_yticklabels(shift_labels)
    ax.tick_params(axis="x", rotation=20)
    ax.set_xlabel("Positive means stressed endpoint is higher")

    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _stress_rank_summaries(
    contact_rows: list[dict[str, str]], load_rows: list[dict[str, str]]
) -> list[dict[str, int | str]]:
    """Return rank-change summaries for the stress endpoints used in the paper."""

    split_low, split_high = _workload_endpoint_values(load_rows, "split_inference")
    industrial_low, industrial_high = _workload_endpoint_values(load_rows, "industrial_iot")
    pairs = [
        (
            contact_rows,
            "eo_dual_stream",
            "contact_profile",
            "mild",
            "stress",
            "EO contact",
        ),
        (
            contact_rows,
            "driving_v2x",
            "contact_profile",
            "mild",
            "stress",
            "Driving contact",
        ),
        (
            load_rows,
            "split_inference",
            "workload_arrival_rate_mean",
            split_low,
            split_high,
            "Split load",
        ),
        (
            load_rows,
            "industrial_iot",
            "workload_arrival_rate_mean",
            industrial_low,
            industrial_high,
            "Industrial load",
        ),
    ]
    summaries: list[dict[str, int | str]] = []
    for rows, scenario, endpoint_key, left, right, label in pairs:
        left_ranks = _score_rank_map(
            row
            for row in rows
            if row["scenario"] == scenario and row[endpoint_key] == left
        )
        right_ranks = _score_rank_map(
            row
            for row in rows
            if row["scenario"] == scenario and row[endpoint_key] == right
        )
        controllers = sorted(set(left_ranks) & set(right_ranks))
        changed = sum(
            1 for controller in controllers if left_ranks[controller] != right_ranks[controller]
        )
        total = len(controllers)
        summaries.append(
            {
                "label": label,
                "stable": total - changed,
                "changed": changed,
                "total": total,
            }
        )
    return summaries


def _stress_metric_shift_matrix(
    contact_rows: list[dict[str, str]], load_rows: list[dict[str, str]]
) -> tuple[list[str], list[str], list[list[float]]]:
    """Return stress-endpoint metric shifts averaged across common controllers."""

    split_low, split_high = _workload_endpoint_values(load_rows, "split_inference")
    industrial_low, industrial_high = _workload_endpoint_values(load_rows, "industrial_iot")
    pairs = [
        (contact_rows, "eo_dual_stream", "contact_profile", "mild", "stress", "EO contact"),
        (
            contact_rows,
            "driving_v2x",
            "contact_profile",
            "mild",
            "stress",
            "Driving contact",
        ),
        (
            load_rows,
            "split_inference",
            "workload_arrival_rate_mean",
            split_low,
            split_high,
            "Split load",
        ),
        (
            load_rows,
            "industrial_iot",
            "workload_arrival_rate_mean",
            industrial_low,
            industrial_high,
            "Industrial load",
        ),
    ]
    metrics = [
        ("completion_ratio", "Completion"),
        ("slo_violation_rate", "SLO"),
        ("latency_p95_s", "p95 (s)"),
        ("buffered_items_peak", "Buffer"),
    ]

    values: list[list[float]] = []
    labels: list[str] = []
    for rows, scenario, endpoint_key, left, right, label in pairs:
        labels.append(label)
        by_key = {
            (row["controller"], row[endpoint_key]): row
            for row in rows
            if row["scenario"] == scenario
        }
        scenario_values: list[float] = []
        controllers = sorted({controller for controller, endpoint in by_key if endpoint in {left, right}})
        for metric, _ in metrics:
            deltas: list[float] = []
            for controller in controllers:
                left_row = by_key.get((controller, left))
                right_row = by_key.get((controller, right))
                if left_row is None or right_row is None:
                    continue
                if metric == "latency_p95_s" and (
                    left_row["valid_for_latency_rank"] != "True"
                    or right_row["valid_for_latency_rank"] != "True"
                ):
                    continue
                deltas.append(as_float(right_row, metric) - as_float(left_row, metric))
            scenario_values.append(sum(deltas) / len(deltas) if deltas else math.nan)
        values.append(scenario_values)
    return [label for _, label in metrics], labels, values


def _workload_endpoint_values(rows: list[dict[str, str]], scenario: str) -> tuple[str, str]:
    """Return the lowest and highest configured workload rates for a scenario."""

    values = sorted(
        {row["workload_arrival_rate_mean"] for row in rows if row["scenario"] == scenario},
        key=float,
    )
    if len(values) < 2:
        raise ValueError(f"Expected at least two workload endpoints for {scenario}")
    return values[0], values[-1]


def _score_rank_map(rows: Any) -> dict[str, int]:
    """Rank controller variants by descending completion-aware score."""

    sorted_rows = sorted(rows, key=_composite_score_or_negative_inf, reverse=True)
    return {row["controller"]: idx + 1 for idx, row in enumerate(sorted_rows)}


def _composite_score_or_negative_inf(row: dict[str, str]) -> float:
    """Return a sortable score for ranking rows."""

    score = as_float(row, "composite_score")
    return score if not math.isnan(score) else -math.inf


def _column_normalized_signed(values: list[list[float]]) -> list[list[float]]:
    """Normalize each metric column to [-1, 1] while preserving the sign."""

    if not values:
        return []
    columns = list(zip(*values))
    max_abs_by_col = [
        max((abs(value) for value in column if not math.isnan(value)), default=0.0)
        for column in columns
    ]
    normalized: list[list[float]] = []
    for row in values:
        normalized_row: list[float] = []
        for col_idx, value in enumerate(row):
            max_abs = max_abs_by_col[col_idx]
            if math.isnan(value) or max_abs == 0.0:
                normalized_row.append(0.0)
            else:
                normalized_row.append(value / max_abs)
        normalized.append(normalized_row)
    return normalized


def plot_eo_trajectory(aggregate_csv: Path, out_dir: Path) -> Path:
    """Plot one EO F2 trajectory comparing two autoscaling controllers."""

    aggregate_rows = read_csv(aggregate_csv)
    output_path = out_dir / "eo_f2_controller_trajectory.pdf"
    run_dirs = _eo_trajectory_run_dirs(aggregate_rows)
    series = {label: _trajectory_series(run_dir) for label, run_dir in run_dirs.items()}

    fig, axes = plt.subplots(3, 1, figsize=(6.7, 4.0), sharex=True)
    styles = {"HPA": ("#1f77b4", "-"), "KEDA": ("#2ca02c", "--")}
    for label, values in series.items():
        color, linestyle = styles[label]
        axes[0].plot(
            values["epoch"],
            values["buffered_items"],
            color=color,
            linestyle=linestyle,
            linewidth=1.3,
            label=label,
        )
        axes[1].plot(
            values["epoch"],
            values["active_workers"],
            color=color,
            linestyle=linestyle,
            linewidth=1.3,
            label=label,
        )
        axes[2].plot(
            values["epoch"],
            values["completed_cumulative"],
            color=color,
            linestyle=linestyle,
            linewidth=1.3,
            label=label,
        )

    axes[0].set_ylabel("Buffered items")
    axes[1].set_ylabel("Active workers")
    axes[2].set_ylabel("Completed tasks")
    axes[2].set_xlabel("Epoch")
    for ax in axes:
        ax.grid(True, color="#e8e8e8", linewidth=0.6)
    axes[0].legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.04), ncol=2)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _eo_trajectory_run_dirs(rows: list[dict[str, str]]) -> dict[str, Path]:
    """Return fair autoscaling-controller runs for the EO trajectory figure."""

    return {
        "HPA": _select_run_dir(
            rows,
            scenario="eo_dual_stream",
            regime="F2",
            controller="hpa",
            seed=0,
        ),
        "KEDA": _select_run_dir(
            rows,
            scenario="eo_dual_stream",
            regime="F2",
            controller="keda",
            seed=0,
        ),
    }


def _main_tradeoff_label_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return the best conventional scale-capable and matched placement-only rows."""

    scale_rows = [
        row for row in rows if row["controller"] in CONVENTIONAL_SCALE_CONTROLLERS
    ]
    placement_rows = [
        row
        for row in rows
        if CONTROLLER_FAMILIES[row["controller"]] == "Placement-only"
        and row["valid_for_latency_rank"] == "True"
    ]
    label_rows = []
    if scale_rows:
        label_rows.append(max(scale_rows, key=lambda row: as_float(row, "composite_score")))
    if placement_rows:
        label_rows.append(max(placement_rows, key=lambda row: as_float(row, "composite_score")))
    return label_rows


def _select_run_dir(
    rows: list[dict[str, str]],
    *,
    scenario: str,
    regime: str,
    controller: str,
    seed: int,
) -> Path:
    """Select one run directory from aggregate rows."""

    matches = [
        row
        for row in rows
        if row["scenario"] == scenario
        and row["evaluation_regime"] == regime
        and row["controller"] == controller
        and int(float(row["seed"])) == int(seed)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one run for {scenario}/{regime}/{controller}/seed{seed}, got {len(matches)}"
        )
    return Path(matches[0]["run_dir"])


def _trajectory_series(run_dir: Path) -> dict[str, list[int]]:
    """Read epoch-level trajectory values from a run directory."""

    epoch_values: list[int] = []
    buffered_items: list[int] = []
    completed_cumulative: list[int] = []
    completed_total = 0

    with (run_dir / "epochs.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                epoch = int(row["epoch"])
                completed_total += int(float(row["completed"]))
                buffer_items = int(float(row["buffer_items"]))
            except ValueError:
                continue
            epoch_values.append(epoch)
            completed_cumulative.append(completed_total)
            buffered_items.append(buffer_items)

    workers_by_epoch: dict[int, int] = {}
    with (run_dir / "decision_trajectory.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            outcome = record.get("outcome", {})
            epoch = int(outcome.get("epoch", record.get("epoch")))
            workers_by_epoch[epoch] = int(outcome.get("active_workers", {}).get("BulkPath", 0))

    return {
        "epoch": epoch_values,
        "buffered_items": buffered_items,
        "completed_cumulative": completed_cumulative,
        "active_workers": [workers_by_epoch.get(epoch, 0) for epoch in epoch_values],
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into dictionaries."""

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(row: dict[str, Any], key: str) -> float:
    """Read a numeric field from a row."""

    value = row.get(key, "")
    return float(value) if value != "" else math.nan


if __name__ == "__main__":
    raise SystemExit(main())
