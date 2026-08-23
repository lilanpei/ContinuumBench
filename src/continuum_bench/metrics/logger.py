from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict


class BenchmarkLogger:
    """JSONL/CSV logger for benchmark runs."""

    def __init__(self, run_dir: Path, metadata: Dict[str, Any]):
        """Create output files for one benchmark run."""

        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.epochs_jsonl_path = self.run_dir / "epochs.jsonl"
        self.decision_trajectory_path = self.run_dir / "decision_trajectory.jsonl"
        self.epochs_csv_path = self.run_dir / "epochs.csv"
        self.metadata_path = self.run_dir / "metadata.json"
        self.summary_path = self.run_dir / "summary.json"

        self._jsonl_file = self.epochs_jsonl_path.open("w", encoding="utf-8")
        self._decision_trajectory_file = self.decision_trajectory_path.open("w", encoding="utf-8")
        self._csv_file = self.epochs_csv_path.open("w", encoding="utf-8", newline="")
        self._csv_writer = csv.DictWriter(
            self._csv_file,
            fieldnames=[
                "epoch",
                "time_s",
                "generated",
                "completed",
                "deadline_miss_count",
                "slo_violation_count",
                "decision_time_ms",
                "buffer_items",
                "buffer_bytes",
                "cross_tier_bytes",
                "delta_k",
                "migrations",
            ],
        )
        self._csv_writer.writeheader()

        with self.metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)

    def log_epoch(self, record: Dict[str, Any]) -> None:
        """Append one epoch record to JSONL and CSV outputs."""

        self._jsonl_file.write(json.dumps(record, sort_keys=True) + "\n")
        self._jsonl_file.flush()

        row = {
            "epoch": int(record.get("epoch", 0)),
            "time_s": float(record.get("time_s", 0.0)),
            "generated": int(record.get("completions", {}).get("generated", 0)),
            "completed": int(record.get("completions", {}).get("count", 0)),
            "deadline_miss_count": int(record.get("completions", {}).get("deadline_miss_count", 0)),
            "slo_violation_count": int(record.get("completions", {}).get("slo_violation_count", 0)),
            "decision_time_ms": float(record.get("decision_time_ms", 0.0)),
            "buffer_items": int(record.get("buffers", {}).get("items", 0)),
            "buffer_bytes": int(record.get("buffers", {}).get("bytes", 0)),
            "cross_tier_bytes": int(record.get("traffic", {}).get("cross_tier_bytes", 0)),
            "delta_k": float(record.get("churn", {}).get("delta_k", 0.0)),
            "migrations": float(record.get("churn", {}).get("migrations", 0.0)),
        }
        self._csv_writer.writerow(row)
        self._csv_file.flush()

    def log_decision_trajectory(self, record: Dict[str, Any]) -> None:
        """Append one controller decision record."""

        self._decision_trajectory_file.write(json.dumps(record, sort_keys=True) + "\n")
        self._decision_trajectory_file.flush()

    def write_summary(self, summary: Dict[str, Any]) -> None:
        """Write the final benchmark summary file."""

        with self.summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)

    def close(self) -> None:
        """Close all open log files."""

        self._jsonl_file.close()
        self._decision_trajectory_file.close()
        self._csv_file.close()

    def __enter__(self) -> "BenchmarkLogger":
        """Return the logger for context-manager use."""

        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        """Close files when leaving the context manager."""

        del exc_type, exc, exc_tb
        self.close()
