from __future__ import annotations

import json
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import yaml

from continuum_bench.controllers import (
    ControllerAction,
    ControllerState,
    HPAController,
    KEDAController,
    LearnedStubController,
    PlacementOnlyController,
    RLMetaController,
    SeparatedLoopsController,
    WorkerPoolManager,
)
from continuum_bench.controllers.extensions import build_extension_controller
from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.infra import (
    ContactSchedule,
    ContactScheduleUpdatePolicy,
    NodeFailureAvailabilityUpdatePolicy,
)
from continuum_bench.metrics import BenchmarkLogger, summarize_epochs
from continuum_bench.placement.eclypse_adapter import EclypsePlacementAdapter
from continuum_bench.realism import detect_evaluation_regime, detect_realism_tier
from continuum_bench.runtime.store_forward import BufferPriority, StoreForwardBuffer
from continuum_bench.scenarios import ScenarioBuild, build_scenario
from continuum_bench.workload import WorkloadStream, build_stream

ensure_eclypse_on_path()

try:
    from eclypse.remote.communication.route import Route as EclypseRoute
except ModuleNotFoundError:
    EclypseRoute = None  # type: ignore[assignment]


@dataclass
class Task:
    task_id: str
    source_id: str
    created_epoch: int
    created_time_s: float
    priority: str
    size_bytes: int
    queue_entered_time_s: float = 0.0
    buffer_entered_time_s: Optional[float] = None
    transport_entered_time_s: Optional[float] = None
    delay_components_s: Dict[str, float] = field(default_factory=dict)

    def clone(self) -> "Task":
        """Return a shallow value copy of the task."""

        return Task(
            task_id=self.task_id,
            source_id=self.source_id,
            created_epoch=self.created_epoch,
            created_time_s=self.created_time_s,
            priority=self.priority,
            size_bytes=self.size_bytes,
            queue_entered_time_s=self.queue_entered_time_s,
            buffer_entered_time_s=self.buffer_entered_time_s,
            transport_entered_time_s=self.transport_entered_time_s,
            delay_components_s=dict(self.delay_components_s),
        )


class _SizedRoutePayload:
    """Minimal payload object whose Eclypse route size equals task bytes."""

    __slots__ = ("size_bytes",)

    def __init__(self, size_bytes: int) -> None:
        """Store the simulated message size in bytes."""

        self.size_bytes = max(0, int(size_bytes))

    def __sizeof__(self) -> int:
        """Return the simulated payload byte size for Eclypse Route.cost()."""

        return self.size_bytes


@dataclass
class InFlightMessage:
    deliver_epoch: int
    target_stage: str
    task: Task


@dataclass
class StageRuntime:
    stage_id: str
    profile: Dict[str, Any]
    role: str
    queue: Deque[Task] = field(default_factory=deque)
    processing_credit: float = 0.0
    route_rr_index: int = 0
    buffers: Dict[str, StoreForwardBuffer] = field(default_factory=dict)


@dataclass
class SimClock:
    now_s: float = 0.0

    def now(self) -> float:
        """Return the current simulated time in seconds."""

        return self.now_s


RESOURCE_DIMENSIONS = ("cpu", "ram", "storage")


class ContinuumBenchRunner:
    """End-to-end deterministic benchmark runner."""

    def __init__(
        self,
        config: Dict[str, Any],
        controller_name: str,
        seed: int,
        out_root: Path,
    ):
        """Initialize a deterministic runner from benchmark configuration."""

        self.config = config
        self.seed = int(seed)
        self.controller_name = controller_name
        self.out_root = Path(out_root)

        random.seed(self.seed)
        self.rng = random.Random(self.seed)

        self.workload_epochs = int(config.get("epochs", 60))
        self.drain_epochs = max(0, int(config.get("drain_epochs", 0)))
        self.epochs = self.workload_epochs + self.drain_epochs
        self.step_seconds = float(config.get("step_seconds", 1.0))

        scenario_name = str(config.get("scenario", "eo_dual_stream"))
        self.scenario: ScenarioBuild = build_scenario(
            name=scenario_name,
            config=config,
            seed=self.seed,
        )

        self.infrastructure = self.scenario.infrastructure
        self.application = self.scenario.application

        self.contact_schedule = ContactSchedule.from_config(
            config.get("infrastructure", {}).get("contact_schedule", {})
        )
        self.contact_edges = list(self.scenario.contact_edges)
        trace_edges = self.contact_schedule.trace_edges()
        if trace_edges:
            self.contact_edges = [
                edge
                for edge in trace_edges
                if self.infrastructure.has_edge(edge[0], edge[1])
            ]
        self._contact_schedule_policy = ContactScheduleUpdatePolicy(
            schedule=self.contact_schedule,
            contact_edges=self.contact_edges,
        )
        self._install_contact_update_policy()

        self.clock = SimClock(now_s=0.0)
        self.stage_runtimes: Dict[str, StageRuntime] = self._build_stage_runtimes()
        self.inflight: List[InFlightMessage] = []
        self._link_bytes_sent_this_epoch: Dict[Tuple[int, Tuple[Tuple[str, str], ...]], int] = {}
        self._traffic_bytes_this_epoch: Dict[str, int] = {}
        self._traffic_link_bytes_this_epoch: Dict[str, int] = {}
        self._failure_rng = random.Random(self.seed + 8101)
        self._failure_cfg = dict(config.get("reliability", {}).get("failures", {}))
        self._active_node_failures: set[str] = set()
        self._node_failure_policy = NodeFailureAvailabilityUpdatePolicy()
        self._install_failure_update_policy()
        self._failure_failed_tasks_this_epoch = 0
        # Tasks lost outside a buffer: in transit to a failed node, or on a hop that has no
        # buffer. Buffer losses are collected from each buffer's ``discarded`` list; both feed
        # the SLO horizon, so discarded work counts as missed.
        self._failure_lost_tasks: List[Task] = []

        self.worker_pool_manager = WorkerPoolManager(
            application=self.application,
            worker_pools=self.scenario.worker_pools,
            min_workers={
                stage: int(pool_cfg.get("min_workers", 0))
                for stage, pool_cfg in config.get("scaling", {}).get("pools", {}).items()
            },
            initial_workers={
                stage: int(pool_cfg.get("initial_workers", 0))
                for stage, pool_cfg in config.get("scaling", {}).get("pools", {}).items()
            },
            startup_delay_s={
                stage: float(pool_cfg.get("startup_delay_s", 0.0))
                for stage, pool_cfg in config.get("scaling", {}).get("pools", {}).items()
            },
            time_fn=self.clock.now,
        )
        self.migration_delay_s = float(config.get("placement", {}).get("migration_delay_s", 0.0))

        self.placement: Dict[str, str] = {}
        self._placement_adapter = EclypsePlacementAdapter(
            infrastructure=self.infrastructure,
            application=self.application,
        )
        self.controller = self._build_controller(controller_name, config)

        self.workload_streams = self._build_workload_streams()

        self.task_counter = 0
        self.last_delivered_created_time_s: Optional[float] = None
        self._completed_sink_tasks: List[Task] = []

        self.epoch_records: List[Dict[str, Any]] = []
        self._last_decision_trajectory_record: Optional[Dict[str, Any]] = None

        self.run_id = (
            time.strftime("%Y%m%d_%H%M%S")
            + f"_{time.time_ns() % 1_000_000_000:09d}_{scenario_name}_{controller_name}_seed{self.seed}"
        )
        self.run_dir = self.out_root / self.run_id

    def run(self) -> Dict[str, Any]:
        """Execute the configured benchmark and persist its artifacts."""

        evaluation = detect_evaluation_regime(self.config)
        metadata = {
            "scenario": self.scenario.name,
            "controller": self.controller_name,
            "seed": self.seed,
            "epochs": self.epochs,
            "workload_epochs": self.workload_epochs,
            "drain_epochs": self.drain_epochs,
            "step_seconds": self.step_seconds,
            "cost_flat_price_per_worker_second": float(
                self.config.get("cost", {}).get("flat_price_per_worker_second", 1.0)
            ),
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "evaluation": evaluation,
            "realism": detect_realism_tier(self.config),
        }

        with BenchmarkLogger(self.run_dir, metadata=metadata) as logger:
            for epoch in range(self.epochs):
                epoch_record = self._run_epoch(epoch)
                logger.log_epoch(epoch_record)
                if self._last_decision_trajectory_record is not None:
                    logger.log_decision_trajectory(self._last_decision_trajectory_record)
                self.epoch_records.append(epoch_record)

            summary = summarize_epochs(metadata, self.epoch_records)
            logger.write_summary(summary)

        return {
            "run_dir": str(self.run_dir),
            "summary": summary,
        }

    def _run_epoch(self, epoch: int) -> Dict[str, Any]:
        """Execute one simulation epoch and return its logged outcome."""

        current_time_s = epoch * self.step_seconds
        self.clock.now_s = current_time_s
        self._link_bytes_sent_this_epoch.clear()
        self._traffic_bytes_this_epoch.clear()
        self._traffic_link_bytes_this_epoch.clear()
        self._failure_failed_tasks_this_epoch = 0
        self._restore_node_failure_availability()

        contact_updates = self._evolve_contact_schedule(current_time_s)
        failure_snapshot = self._apply_failure_model(epoch)
        self._deliver_inflight(epoch)

        state = self._build_controller_state(epoch=epoch, time_s=current_time_s)
        decision_start = time.perf_counter()
        placement_outage = False
        try:
            action = self.controller.step(state)
        except ValueError as exc:
            if not self._is_transient_placement_outage(exc):
                raise
            placement_outage = True
            action = ControllerAction(
                metadata={
                    "controller": self.controller_name,
                    "placement_outage": True,
                    "error": str(exc),
                }
            )
        decision_time_ms = (time.perf_counter() - decision_start) * 1000.0

        if placement_outage:
            churn = {
                "delta_k": 0.0,
                "migrations": 0.0,
                "startup_delay_s": 0.0,
                "migration_delay_s": 0.0,
            }
        else:
            churn = self._apply_action(action)

        self._flush_buffers(epoch)
        generated = (
            {"generated": 0, "generated_by_priority": {}}
            if placement_outage or self._is_drain_epoch(epoch)
            else self._generate_sources(epoch)
        )
        resource_snapshot = self._compute_resource_snapshot()
        energy_snapshot = self._compute_energy_snapshot(resource_snapshot)
        completions = [] if placement_outage else self._process_transforms_and_sink(epoch, resource_snapshot)
        slo_eligible = {
            "count": 0,
            "by_priority": {},
            "unfinished_violations": 0,
            "unfinished_violations_by_priority": {},
        }
        if epoch == self.epochs - 1:
            slo_eligible = self._count_horizon_slo_eligible_tasks(run_end_time_s=(epoch + 1) * self.step_seconds)

        deadline_miss_count = 0
        slo_violation_count = 0
        completion_by_priority: Dict[str, Dict[str, int]] = {}
        for completion in completions:
            priority = str(completion.get("priority", "normal"))
            stats = completion_by_priority.setdefault(
                priority,
                {
                    "completed": 0,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 0,
                },
            )
            stats["completed"] += 1
            if bool(completion.get("deadline_miss", False)):
                deadline_miss_count += 1
                stats["deadline_miss_count"] += 1
            if bool(completion.get("slo_violation", False)):
                slo_violation_count += 1
                stats["slo_violation_count"] += 1

        for priority, count in slo_eligible.get("unfinished_violations_by_priority", {}).items():
            stats = completion_by_priority.setdefault(
                priority,
                {
                    "completed": 0,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 0,
                },
            )
            stats["slo_violation_count"] += int(count)

        aoi_proxy_s = self._compute_aoi_proxy(epoch)
        buffers_snapshot = self._aggregate_buffer_snapshots()
        failure_snapshot["failed_tasks"] = int(self._failure_failed_tasks_this_epoch)
        reliability_snapshot = self._compute_reliability_snapshot(
            generated_count=int(generated.get("generated", 0)),
            completed_count=len(completions),
            buffers_snapshot=buffers_snapshot,
            failure_failed_tasks=int(failure_snapshot.get("failed_tasks", 0)),
        )

        outcome = {
            "epoch": epoch,
            "time_s": current_time_s,
            "contact_state": {
                "active_links": sum(
                    1 for upd in contact_updates.values() if float(upd.get("contact_open", 0.0)) > 0.0
                ),
                "total_links": len(contact_updates),
            },
            "decision_time_ms": decision_time_ms,
            "placement": dict(self.placement),
            "active_workers": {
                stage: snap.active_workers
                for stage, snap in self.worker_pool_manager.snapshots().items()
            },
            "ready_workers": {
                stage: snap.ready_workers
                for stage, snap in self.worker_pool_manager.snapshots().items()
            },
            "scaling_actions": [
                {"stage": scale.stage, "active_workers": scale.active_workers}
                for scale in action.scale
            ],
            "queues": {
                stage_id: len(runtime.queue)
                for stage_id, runtime in self.stage_runtimes.items()
            },
            "oldest_age_s": {
                stage_id: self._oldest_age(runtime.queue, current_time_s)
                for stage_id, runtime in self.stage_runtimes.items()
            },
            "completions": {
                "generated": int(generated.get("generated", 0)),
                "generated_by_priority": dict(generated.get("generated_by_priority", {})),
                "slo_eligible_count": int(slo_eligible.get("count", 0)),
                "slo_eligible_by_priority": dict(slo_eligible.get("by_priority", {})),
                "count": len(completions),
                "latencies_s": [c["latency_s"] for c in completions],
                "age_at_delivery_s": [c["age_at_delivery_s"] for c in completions],
                "delay_components_s": self._completion_delay_components(completions),
                "deadline_miss_count": deadline_miss_count,
                "slo_violation_count": slo_violation_count + int(slo_eligible.get("unfinished_violations", 0)),
                "per_priority": completion_by_priority,
            },
            "traffic": {
                "cross_tier_bytes": int(sum(self._traffic_bytes_this_epoch.values())),
                "by_tier_pair": dict(self._traffic_bytes_this_epoch),
            },
            "freshness": {
                "aoi_proxy_s": aoi_proxy_s,
            },
            "resources": resource_snapshot,
            "energy": energy_snapshot,
            "reliability": reliability_snapshot,
            "failures": failure_snapshot,
            "buffers": buffers_snapshot,
            "churn": churn,
            "placement_outage": placement_outage,
        }
        outcome["controller_reward"] = self._compute_controller_reward(outcome)

        self._last_decision_trajectory_record = {
            "epoch": epoch,
            "time_s": current_time_s,
            "state": self._serialize_controller_state(state),
            "action": self._serialize_controller_action(action),
            "outcome": outcome,
            "reward": outcome["controller_reward"],
        }

        return outcome

    def _is_transient_placement_outage(self, exc: ValueError) -> bool:
        """Return whether a placement error is caused by active failure events."""

        if self.placement:
            return False
        if not self._active_node_failures:
            return False
        message = str(exc)
        return "could not place active services" in message

    def _serialize_controller_state(self, state: ControllerState) -> Dict[str, Any]:
        """Convert controller state into JSON-friendly primitives."""

        return {
            "epoch": state.epoch,
            "time_s": state.time_s,
            "seed": state.seed,
            "placement": dict(state.placement),
            "queue_depths": dict(state.queue_depths),
            "oldest_age_s": dict(state.oldest_age_s),
            "ttl_pressure": dict(state.ttl_pressure),
            "buffer_items": dict(state.buffer_items),
            "buffer_bytes": dict(state.buffer_bytes),
            "worker_pools": {
                stage: {
                    "stage": snapshot.stage,
                    "min_workers": snapshot.min_workers,
                    "max_workers": snapshot.max_workers,
                    "active_workers": snapshot.active_workers,
                    "total_workers": snapshot.total_workers,
                    "active_worker_ids": list(snapshot.active_worker_ids),
                    "inactive_worker_ids": list(snapshot.inactive_worker_ids),
                    "ready_workers": snapshot.ready_workers,
                    "ready_worker_ids": list(snapshot.ready_worker_ids),
                    "starting_workers": snapshot.starting_workers,
                    "starting_worker_ids": list(snapshot.starting_worker_ids),
                }
                for stage, snapshot in state.worker_pools.items()
            },
            "metadata": self._json_ready(state.metadata),
        }

    def _serialize_controller_action(self, action: ControllerAction) -> Dict[str, Any]:
        """Convert a controller action into JSON-friendly primitives."""

        return {
            "scale": [
                {"stage": scale.stage, "active_workers": scale.active_workers}
                for scale in action.scale
            ],
            "placement": dict(action.placement or {}),
            "metadata": self._json_ready(action.metadata),
        }

    def _json_ready(self, value: Any) -> Any:
        """Recursively normalize values for JSON serialization."""

        if isinstance(value, dict):
            return {str(key): self._json_ready(val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_ready(item) for item in value]
        if isinstance(value, set):
            return [self._json_ready(item) for item in sorted(value, key=repr)]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return repr(value)

    def _build_stage_runtimes(self) -> Dict[str, StageRuntime]:
        """Create runtime state containers for every application stage."""

        runtimes: Dict[str, StageRuntime] = {}

        for stage_id in self.application.nodes:
            service = self.application.services.get(stage_id)
            role = getattr(getattr(service, "role", None), "name", "TRANSFORM").lower()
            profile = dict(self.scenario.stage_profiles.get(stage_id, {}))

            successors = [dst for src, dst in self.application.edges if src == stage_id]
            buffers: Dict[str, StoreForwardBuffer] = {}
            buffer_cfg = profile.get("buffer", {})
            for target in successors:
                buffers[target] = StoreForwardBuffer(
                    max_size_bytes=int(buffer_cfg.get("max_size_bytes", 128 * 1024 * 1024)),
                    max_items=int(buffer_cfg.get("max_items", 10_000)),
                    on_overflow=str(buffer_cfg.get("on_overflow", "drop_oldest_low")),
                    time_fn=self.clock.now,
                )

            runtimes[stage_id] = StageRuntime(
                stage_id=stage_id,
                profile=profile,
                role=role,
                buffers=buffers,
            )

        return runtimes

    def _build_controller(self, controller_name: str, config: Dict[str, Any]):
        """Instantiate the controller selected by the benchmark config."""

        name = controller_name.lower()

        placement_cfg = config.get("placement", {})
        scaling_cfg = config.get("scaling", {})
        autoscaling_cfg = config.get("autoscaling", {})
        learned_cfg = config.get("learned_controller", {})

        if name in {"separated_loops", "joint", "joint_baseline"}:
            return SeparatedLoopsController(
                placement_strategy=str(placement_cfg.get("strategy", "best_fit")),
                place_every_epochs=int(placement_cfg.get("place_every_epochs", 3)),
                queue_up_threshold=int(scaling_cfg.get("queue_up_threshold", 20)),
                queue_down_threshold=int(scaling_cfg.get("queue_down_threshold", 5)),
                age_up_threshold_s=float(scaling_cfg.get("age_up_threshold_s", 20.0)),
                age_down_threshold_s=float(scaling_cfg.get("age_down_threshold_s", 5.0)),
                ttl_pressure_up_threshold=float(scaling_cfg.get("ttl_pressure_up_threshold", 0.25)),
                ttl_pressure_down_threshold=float(scaling_cfg.get("ttl_pressure_down_threshold", 0.05)),
                cooldown_epochs=int(scaling_cfg.get("cooldown_epochs", 2)),
                scale_step=int(scaling_cfg.get("scale_step", 1)),
                replan_on_scale=bool(placement_cfg.get("replan_on_scale", True)),
                periodic_replan=bool(placement_cfg.get("periodic_replan", True)),
            )

        if name == "hpa":
            hpa_cfg = autoscaling_cfg.get("hpa", {})
            return HPAController(
                placement_strategy=str(placement_cfg.get("strategy", "best_fit")),
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 3)),
                target_utilization=float(hpa_cfg.get("target_utilization", 0.7)),
                tolerance=float(hpa_cfg.get("tolerance", 0.1)),
                cooldown_epochs=int(
                    hpa_cfg.get("cooldown_epochs", scaling_cfg.get("cooldown_epochs", 1))
                ),
                min_replicas=int(hpa_cfg.get("min_replicas", 1)),
                replan_on_scale=bool(placement_cfg.get("replan_on_scale", True)),
                periodic_replan=bool(placement_cfg.get("periodic_replan", True)),
            )

        if name == "keda":
            keda_cfg = autoscaling_cfg.get("keda", {})
            return KEDAController(
                placement_strategy=str(placement_cfg.get("strategy", "best_fit")),
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 3)),
                queue_target_per_worker=int(keda_cfg.get("queue_target_per_worker", 10)),
                activation_queue_length=int(keda_cfg.get("activation_queue_length", 1)),
                min_replicas=int(keda_cfg.get("min_replicas", 0)),
                cooldown_epochs=int(
                    keda_cfg.get("cooldown_epochs", scaling_cfg.get("cooldown_epochs", 2))
                ),
                replan_on_scale=bool(placement_cfg.get("replan_on_scale", True)),
                periodic_replan=bool(placement_cfg.get("periodic_replan", True)),
            )

        if name == "learned_stub":
            return LearnedStubController(
                placement_strategy=str(placement_cfg.get("strategy", "best_fit")),
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 3)),
                queue_weight=float(learned_cfg.get("queue_weight", 1.0)),
                age_weight=float(learned_cfg.get("age_weight", 1.0)),
                ttl_weight=float(learned_cfg.get("ttl_weight", 1.0)),
                scale_up_score=float(learned_cfg.get("scale_up_score", 1.0)),
                scale_down_score=float(learned_cfg.get("scale_down_score", 0.15)),
                scale_step=int(learned_cfg.get("scale_step", scaling_cfg.get("scale_step", 1))),
                cooldown_epochs=int(
                    learned_cfg.get("cooldown_epochs", scaling_cfg.get("cooldown_epochs", 1))
                ),
            )

        if name in {"rl_meta", "rl_meta_controller"}:
            rl_meta_cfg = config.get("rl_meta_controller", {})
            return RLMetaController(
                placement_strategies=tuple(
                    rl_meta_cfg.get(
                        "placement_strategies",
                        [
                            str(placement_cfg.get("strategy", "best_fit")),
                            "min_latency",
                            "cost_greedy",
                        ],
                    )
                ),
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 3)),
                policy=str(rl_meta_cfg.get("policy", "threshold")),
                action_sequence=tuple(rl_meta_cfg.get("action_sequence", ())),
                queue_weight=float(rl_meta_cfg.get("queue_weight", 1.0)),
                age_weight=float(rl_meta_cfg.get("age_weight", 0.1)),
                ttl_weight=float(rl_meta_cfg.get("ttl_weight", 2.0)),
                scale_up_score=float(rl_meta_cfg.get("scale_up_score", 1.0)),
                scale_down_score=float(rl_meta_cfg.get("scale_down_score", 0.15)),
                scale_step=int(rl_meta_cfg.get("scale_step", scaling_cfg.get("scale_step", 1))),
                cooldown_epochs=int(
                    rl_meta_cfg.get("cooldown_epochs", scaling_cfg.get("cooldown_epochs", 1))
                ),
            )

        if name in {"static", "round_robin", "best_fit", "all_local", "all_edge", "all_cloud"}:
            return PlacementOnlyController(
                strategy=name,
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 1)),
                static_mapping=config.get("static_mapping", {}),
            )

        if name == "no_scaling":
            return PlacementOnlyController(
                strategy=str(placement_cfg.get("strategy", "best_fit")),
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 1)),
                static_mapping=config.get("static_mapping", {}),
                freeze_after_initial=True,
            )

        if name.startswith("placement_only"):
            strategy = "round_robin"
            if ":" in name:
                strategy = name.split(":", maxsplit=1)[1]
            return PlacementOnlyController(
                strategy=strategy,
                replan_every_epochs=int(placement_cfg.get("place_every_epochs", 1)),
                static_mapping=config.get("static_mapping", {}),
            )

        extension = build_extension_controller(name, config, self)
        if extension is not None:
            return extension

        raise ValueError(
            "controller must be one of: separated_loops, static, round_robin, best_fit, "
            "all_local, all_edge, all_cloud, no_scaling, hpa, keda, "
            "learned_stub, rl_meta, placement_only:<strategy>"
        )

    def _build_workload_streams(self) -> Dict[str, WorkloadStream]:
        """Create workload generators for each source stage."""

        workload_cfg = self.config.get("workload", {})
        stream_cfg = workload_cfg.get("streams", {})
        repeat_piecewise = bool(workload_cfg.get("repeat", False))

        streams: Dict[str, WorkloadStream] = {}
        for idx, source_stage in enumerate(self.scenario.source_stages):
            key = self.scenario.source_stream_map.get(source_stage, source_stage)
            cfg = dict(stream_cfg.get(
                key,
                {
                    "generator": "piecewise",
                    "segments": [{"epochs": self.epochs, "rate": 1.0}],
                },
            ))
            generator = str(cfg.get("generator", "piecewise")).lower()
            if repeat_piecewise and generator in {"piecewise", "regime", "regimes"}:
                cfg.setdefault("repeat", True)
            streams[source_stage] = build_stream(cfg, seed=self.seed + idx * 997)

        return streams

    def _build_controller_state(self, epoch: int, time_s: float) -> ControllerState:
        """Assemble the observable system state for one controller step."""

        queue_depths = {
            stage_id: len(runtime.queue)
            for stage_id, runtime in self.stage_runtimes.items()
        }
        oldest_age_s = {
            stage_id: self._oldest_age(runtime.queue, time_s)
            for stage_id, runtime in self.stage_runtimes.items()
        }

        buffer_items = {}
        buffer_bytes = {}
        ttl_pressure = {}
        for stage_id, runtime in self.stage_runtimes.items():
            items, bytes_val, pressure = self._buffer_snapshot(runtime)
            buffer_items[stage_id] = items
            buffer_bytes[stage_id] = bytes_val
            ttl_pressure[stage_id] = pressure

        pool_queue_depths: Dict[str, int] = {}
        pool_oldest_age_s: Dict[str, float] = {}
        pool_ttl_pressure: Dict[str, float] = {}
        pool_workers: Dict[str, List[str]] = {}
        for stage, _snapshot in self.worker_pool_manager.snapshots().items():
            workers = self.worker_pool_manager.workers_for_stage(stage)
            pool_workers[stage] = workers
            buffered_items, buffered_oldest_age_s, buffered_ttl_pressure = self._pool_buffer_snapshot(stage)
            pool_queue_depths[stage] = sum(queue_depths.get(worker, 0) for worker in workers) + buffered_items
            pool_oldest_age_s[stage] = max(
                max(oldest_age_s.get(worker, 0.0) for worker in workers) if workers else 0.0,
                buffered_oldest_age_s,
            )
            pool_ttl_pressure[stage] = max(
                max(ttl_pressure.get(worker, 0.0) for worker in workers) if workers else 0.0,
                buffered_ttl_pressure,
            )

        queue_depths.update(pool_queue_depths)
        oldest_age_s.update(pool_oldest_age_s)
        ttl_pressure.update(pool_ttl_pressure)

        return ControllerState(
            epoch=epoch,
            time_s=time_s,
            seed=self.seed,
            application=self.application,
            infrastructure=self.infrastructure,
            placement=dict(self.placement),
            queue_depths=queue_depths,
            oldest_age_s=oldest_age_s,
            ttl_pressure=ttl_pressure,
            buffer_items=buffer_items,
            buffer_bytes=buffer_bytes,
            worker_pools=self.worker_pool_manager.snapshots(),
            metadata={
                "active_services": self.worker_pool_manager.active_service_ids(),
                "ready_services": self.worker_pool_manager.ready_service_ids(),
                "service_base_attrs": {
                    service_id: self.worker_pool_manager.base_attrs_for(service_id)
                    for service_id in self.application.nodes
                },
                "failed_nodes": sorted(self._active_node_failures),
                "pool_workers": pool_workers,
                "pool_queue_depths": pool_queue_depths,
                "pool_oldest_age_s": pool_oldest_age_s,
                "pool_ttl_pressure": pool_ttl_pressure,
                "placement_planner": self._placement_adapter.plan_mapping,
            },
        )

    def _apply_action(self, action: ControllerAction) -> Dict[str, float]:
        """Apply controller decisions and return churn metrics."""

        delta_k_total = 0.0
        for scale in action.scale:
            before = self.worker_pool_manager.active_count(scale.stage)
            self.worker_pool_manager.set_active_workers(scale.stage, scale.active_workers)
            after = self.worker_pool_manager.active_count(scale.stage)
            delta_k_total += abs(after - before)

        startup_delay_total_s = sum(
            max(self.step_seconds, math.ceil(delay_s / self.step_seconds) * self.step_seconds)
            for delay_s in self.worker_pool_manager.consume_startup_delay_events()
        )

        old_placement = dict(self.placement)

        if action.placement is not None:
            self.placement.update(action.placement)

        self._validate_placement_for_active_services(force=action.placement is not None)

        active_services = set(self.worker_pool_manager.active_service_ids())
        migrations = 0
        for service_id in active_services:
            old_node = old_placement.get(service_id)
            new_node = self.placement.get(service_id)
            if old_node is not None and new_node is not None and old_node != new_node:
                migrations += 1

        return {
            "delta_k": float(delta_k_total),
            "migrations": float(migrations),
            "startup_delay_s": float(startup_delay_total_s),
            "migration_delay_s": float(migrations * self.migration_delay_s),
        }

    def _validate_placement_for_active_services(self, *, force: bool = False) -> None:
        """Validate that controller/Eclypse placement covers active services."""

        self.placement = self._placement_adapter.sync_mapping(
            self.placement,
            active_services=self.worker_pool_manager.active_service_ids(),
            force=force,
        )

    def _deliver_inflight(self, epoch: int) -> None:
        """Deliver messages whose latency budget has elapsed."""

        remaining: List[InFlightMessage] = []
        for message in self.inflight:
            if message.deliver_epoch > epoch:
                # Still in transit: retain regardless of a transient target outage this epoch.
                # A message is lost only if its target is unavailable at delivery time.
                remaining.append(message)
                continue
            target_node = self.placement.get(message.target_stage)
            if target_node is not None and self._is_node_down(target_node):
                self._failure_failed_tasks_this_epoch += 1
                self._failure_lost_tasks.append(message.task)
                continue
            self._add_task_delay(
                message.task,
                "transport_s",
                current_time_s=epoch * self.step_seconds,
                start_time_s=message.task.transport_entered_time_s,
            )
            message.task.transport_entered_time_s = None
            message.task.queue_entered_time_s = epoch * self.step_seconds
            self.stage_runtimes[message.target_stage].queue.append(message.task)
        self.inflight = remaining

    def _flush_buffers(self, epoch: int) -> None:
        """Retry buffered transmissions that fit the current link budget."""

        for stage_id, runtime in self.stage_runtimes.items():
            for target_id, buffer in runtime.buffers.items():
                buffer.clean_expired()
                available, bw_mbps, latency_ms = self._resolve_link(stage_id, target_id)
                if not available:
                    continue

                max_bytes = self._bandwidth_to_bytes(bw_mbps)
                if max_bytes >= 10**18:
                    dequeue_limit = None
                    usage_key = None
                else:
                    usage_key = (epoch, self._bandwidth_budget_key(stage_id, target_id))
                    remaining_bytes = max(0, max_bytes - self._link_bytes_sent_this_epoch.get(usage_key, 0))
                    if remaining_bytes <= 0:
                        continue
                    dequeue_limit = remaining_bytes

                items = buffer.dequeue(max_bytes=dequeue_limit, count_transmitted=False)
                if usage_key is not None:
                    transmitted_bytes = sum(max(1, int(item.size_bytes)) for item in items)
                    self._link_bytes_sent_this_epoch[usage_key] = (
                        self._link_bytes_sent_this_epoch.get(usage_key, 0) + transmitted_bytes
                    )

                for item in items:
                    payload = item.data
                    task = payload["task"] if isinstance(payload, dict) else payload
                    if isinstance(task, Task):
                        self._add_task_delay(
                            task,
                            "buffer_wait_s",
                            current_time_s=epoch * self.step_seconds,
                            start_time_s=task.buffer_entered_time_s,
                        )
                        task.buffer_entered_time_s = None
                    resolved_target_id = target_id
                    route = self.scenario.route_specs.get(stage_id)
                    mode = getattr(route, "mode", None) if route is not None else None
                    if mode in ("triage", "balance"):
                        pool_stage = route.low_pool if mode == "triage" else route.balance_pool
                        pool_targets = set(self.worker_pool_manager.workers_for_stage(pool_stage))
                        if target_id in pool_targets:
                            remapped_targets = self._pick_pool_target(stage_id, pool_stage)
                            if remapped_targets:
                                resolved_target_id = remapped_targets[0]

                    available, bw_mbps, latency_ms = self._resolve_link(stage_id, resolved_target_id)
                    if not available:
                        if isinstance(task, Task):
                            task.buffer_entered_time_s = epoch * self.step_seconds
                        # Re-buffer a failed delivery attempt without re-counting it as newly
                        # buffered; it was already counted when first enqueued.
                        buffer.enqueue(
                            data={"task": task, "target": target_id},
                            priority=item.priority,
                            size_bytes=item.size_bytes,
                            ttl=max(0.0, item.ttl - item.age),
                            count_buffered=False,
                        )
                        continue

                    # Match the fresh-send path: size-aware route cost, so a buffered
                    # item's transfer time scales with payload and link rate instead of
                    # depending on latency alone.
                    delay_epochs = (
                        self._message_delay_epochs(stage_id, resolved_target_id, task, latency_ms)
                        if isinstance(task, Task)
                        else self._latency_to_epochs(latency_ms)
                    )
                    if isinstance(task, Task):
                        task.transport_entered_time_s = epoch * self.step_seconds
                    self.inflight.append(
                        InFlightMessage(
                            deliver_epoch=epoch + delay_epochs,
                            target_stage=resolved_target_id,
                            task=task,
                        )
                    )
                    # Count as transmitted only now that the item is actually handed to inflight.
                    buffer.mark_transmitted(item)
                    self._record_traffic(stage_id, resolved_target_id, item.size_bytes)

    def _generate_sources(self, epoch: int) -> Dict[str, Any]:
        """Generate source tasks for the current epoch."""

        now_s = epoch * self.step_seconds
        generated = 0
        generated_by_priority: Dict[str, int] = {}
        for source_stage in self.scenario.source_stages:
            stream = self.workload_streams[source_stage]
            count = stream.sample(epoch)
            profile = self.stage_runtimes[source_stage].profile
            size_bytes = int(profile.get("output_size_bytes", 1024))

            for _ in range(count):
                self.task_counter += 1
                task = Task(
                    task_id=f"task-{self.task_counter}",
                    source_id=source_stage,
                    created_epoch=epoch,
                    created_time_s=now_s,
                    priority=self._sample_priority(profile),
                    size_bytes=size_bytes,
                    queue_entered_time_s=now_s,
                )
                generated += 1
                generated_by_priority[task.priority] = generated_by_priority.get(task.priority, 0) + 1
                self._route_task(source_stage, task, epoch)
        return {
            "generated": generated,
            "generated_by_priority": generated_by_priority,
        }

    def _is_drain_epoch(self, epoch: int) -> bool:
        """Return whether this epoch is after the workload-arrival window."""

        return int(epoch) >= self.workload_epochs

    def _process_transforms_and_sink(
        self,
        epoch: int,
        resource_snapshot: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Process queued work and collect sink completions for the epoch."""

        completions: List[Dict[str, Any]] = []
        completion_time_s = (epoch + 1) * self.step_seconds
        ready_service_ids = set(self.worker_pool_manager.ready_service_ids())

        for stage_id in self._topological_stage_order():
            runtime = self.stage_runtimes[stage_id]
            if runtime.role == "source":
                continue

            workers = 1
            node_id = self.placement.get(stage_id)
            if stage_id not in ready_service_ids or (node_id is not None and self._is_node_down(node_id)):
                workers = 0

            processing_time_s = max(1e-6, float(runtime.profile.get("processing_time_s", 1.0)))
            node_factor = self._stage_processing_factor(stage_id, resource_snapshot)
            runtime.processing_credit += workers * node_factor * (self.step_seconds / processing_time_s)
            capacity = int(runtime.processing_credit)
            if capacity <= 0:
                continue
            runtime.processing_credit -= capacity

            for _ in range(min(capacity, len(runtime.queue))):
                task = runtime.queue.popleft()
                processing_start_time_s = epoch * self.step_seconds
                self._add_task_delay(
                    task,
                    "queue_wait_s",
                    current_time_s=processing_start_time_s,
                    start_time_s=task.queue_entered_time_s,
                )
                self._add_task_delay(
                    task,
                    "processing_s",
                    current_time_s=completion_time_s,
                    value_s=self.step_seconds,
                )
                alpha = float(runtime.profile.get("alpha", 1.0))
                task.size_bytes = max(1, int(task.size_bytes * alpha))

                if stage_id == self.scenario.sink_stage:
                    latency_s = completion_time_s - task.created_time_s
                    deadline_s = float(runtime.profile.get("deadline_s", runtime.profile.get("deadline", float("inf"))))
                    deadline_miss = latency_s > deadline_s
                    self._completed_sink_tasks.append(task.clone())
                    completions.append(
                        {
                            "latency_s": float(latency_s),
                            "age_at_delivery_s": float(latency_s),
                            "priority": task.priority,
                            "deadline_miss": bool(deadline_miss),
                            "slo_violation": bool(deadline_miss),
                            "delay_components_s": dict(task.delay_components_s),
                        }
                    )
                    self.last_delivered_created_time_s = task.created_time_s
                else:
                    self._route_task(stage_id, task, epoch)

        return completions

    def _count_horizon_slo_eligible_tasks(self, run_end_time_s: float) -> Dict[str, Any]:
        """Count tasks whose SLO should have resolved by run end."""

        sink_runtime = self.stage_runtimes[self.scenario.sink_stage]
        deadline_s = float(
            sink_runtime.profile.get("deadline_s", sink_runtime.profile.get("deadline", float("inf")))
        )
        if deadline_s < 0 or math.isinf(deadline_s):
            return {
                "count": 0,
                "by_priority": {},
                "unfinished_violations": 0,
                "unfinished_violations_by_priority": {},
            }

        eligible_count = 0
        eligible_by_priority: Dict[str, int] = {}
        unfinished_violations = 0
        unfinished_violations_by_priority: Dict[str, int] = {}

        for task in self._completed_sink_tasks:
            if task.created_time_s + deadline_s > run_end_time_s:
                continue
            eligible_count += 1
            eligible_by_priority[task.priority] = eligible_by_priority.get(task.priority, 0) + 1

        seen_task_ids = set()
        # Work still in the system, plus work the run destroyed (buffer overflow, TTL
        # expiry, retry exhaustion, node outage, a hop without a buffer). Both are equally
        # "not delivered on time": counting only the former let a controller improve its SLO
        # rate by discarding tasks, since a discarded task left the queues/buffers/inflight
        # sets and so was never eligible.
        for task in self._horizon_unfinished_tasks() + self._horizon_lost_tasks():
            if task.task_id in seen_task_ids or task.created_time_s + deadline_s > run_end_time_s:
                continue
            seen_task_ids.add(task.task_id)
            eligible_count += 1
            eligible_by_priority[task.priority] = eligible_by_priority.get(task.priority, 0) + 1
            unfinished_violations += 1
            unfinished_violations_by_priority[task.priority] = (
                unfinished_violations_by_priority.get(task.priority, 0) + 1
            )

        return {
            "count": eligible_count,
            "by_priority": eligible_by_priority,
            "unfinished_violations": unfinished_violations,
            "unfinished_violations_by_priority": unfinished_violations_by_priority,
        }

    def _horizon_unfinished_tasks(self) -> List[Task]:
        """Collect unfinished tasks from queues, buffers, and inflight links."""

        tasks: List[Task] = []
        for runtime in self.stage_runtimes.values():
            tasks.extend(list(runtime.queue))
            for buffer in runtime.buffers.values():
                for queue in buffer._queues.values():  # pylint: disable=protected-access
                    for item in queue:
                        payload = item.data
                        task = payload.get("task") if isinstance(payload, dict) else payload
                        if isinstance(task, Task):
                            tasks.append(task)
        for message in self.inflight:
            tasks.append(message.task)
        return tasks

    def _horizon_lost_tasks(self) -> List[Task]:
        """Collect tasks the run destroyed rather than delivered.

        Covers every buffer loss path (overflow rejection, eviction, retry exhaustion,
        TTL expiry), tasks lost in transit to a node outage, and tasks sent on a hop that
        has no buffer.
        """

        tasks: List[Task] = list(self._failure_lost_tasks)
        for runtime in self.stage_runtimes.values():
            for buffer in runtime.buffers.values():
                for item in buffer.discarded:
                    payload = item.data
                    task = payload.get("task") if isinstance(payload, dict) else payload
                    if isinstance(task, Task):
                        tasks.append(task)
        return tasks

    def _route_task(self, stage_id: str, task: Task, epoch: int) -> None:
        """Route a task from one stage to its downstream targets."""

        route = self.scenario.route_specs.get(stage_id)
        if route is None:
            return

        targets: List[str] = []
        if route.mode == "single" and route.target:
            targets = [route.target]
        elif route.mode == "triage":
            high_prios = set(
                self.stage_runtimes[stage_id].profile.get("triage_high_priorities", ["critical", "high"])
            )
            if task.priority in high_prios:
                targets = [route.high_target]
            else:
                targets = self._pick_pool_target(stage_id, route.low_pool)
        elif route.mode == "balance":
            targets = self._pick_pool_target(stage_id, route.balance_pool)

        for target in targets:
            self._send_or_buffer(stage_id, target, task.clone(), epoch)

    def _pick_pool_target(self, stage_id: str, pool_stage: str) -> List[str]:
        """Select a pool worker for routing, preferring ready workers."""

        pool_workers = self.worker_pool_manager.workers_for_stage(pool_stage)
        workers = self.worker_pool_manager.ready_workers(pool_stage)
        if not workers:
            return pool_workers[:1]

        runtime = self.stage_runtimes[stage_id]
        chosen = workers[runtime.route_rr_index % len(workers)]
        runtime.route_rr_index += 1
        return [chosen]

    def _send_or_buffer(self, src_stage: str, dst_stage: str, task: Task, epoch: int) -> None:
        """Send a task immediately or enqueue it in the source buffer."""

        available, bw_mbps, latency_ms = self._resolve_link(src_stage, dst_stage)
        if available:
            max_bytes = self._bandwidth_to_bytes(bw_mbps)
            if max_bytes < 10**18:
                usage_key = (epoch, self._bandwidth_budget_key(src_stage, dst_stage))
                bytes_sent = self._link_bytes_sent_this_epoch.get(usage_key, 0)
                if bytes_sent + task.size_bytes > max_bytes:
                    # A message larger than one epoch's budget fails this test in every
                    # epoch and stalls forever. Admit it alone on an otherwise idle link;
                    # its serialization time is carried by the transfer delay below.
                    if bytes_sent == 0 and task.size_bytes > max_bytes:
                        self._link_bytes_sent_this_epoch[usage_key] = task.size_bytes
                    else:
                        available = False
                else:
                    self._link_bytes_sent_this_epoch[usage_key] = bytes_sent + task.size_bytes

        if available:
            delay_epochs = self._message_delay_epochs(src_stage, dst_stage, task, latency_ms)
            self.inflight.append(
                InFlightMessage(
                    deliver_epoch=epoch + delay_epochs,
                    target_stage=dst_stage,
                    task=task,
                )
            )
            task.transport_entered_time_s = epoch * self.step_seconds
            self._record_traffic(src_stage, dst_stage, task.size_bytes)
            return

        runtime = self.stage_runtimes[src_stage]
        buffer = runtime.buffers.get(dst_stage)
        if buffer is None:
            # No buffer exists for this hop, so the task cannot be retried and is lost here.
            # Record it, so that the loss counts as a failure and as an SLO violation.
            self._failure_lost_tasks.append(task)
            self._failure_failed_tasks_this_epoch += 1
            return

        payload = {
            "task": task,
            "target": dst_stage,
        }
        task.buffer_entered_time_s = epoch * self.step_seconds
        buffer.enqueue(
            data=payload,
            priority=self._buffer_priority(task.priority),
            size_bytes=max(1, int(task.size_bytes)),
            ttl=float(runtime.profile.get("buffer_ttl", 300.0)),
        )

    def _add_task_delay(
        self,
        task: Task,
        component: str,
        *,
        current_time_s: float,
        start_time_s: Optional[float] = None,
        value_s: Optional[float] = None,
    ) -> None:
        """Accumulate one epoch-quantized residence-time component on a task."""

        if value_s is None:
            if start_time_s is None:
                return
            value_s = float(current_time_s) - float(start_time_s)
        task.delay_components_s[component] = (
            float(task.delay_components_s.get(component, 0.0)) + max(0.0, float(value_s))
        )

    def _completion_delay_components(self, completions: list[dict[str, Any]]) -> dict[str, list[float]]:
        """Return per-completion delay component arrays for epoch logs."""

        components = [
            "queue_wait_s",
            "processing_s",
            "transport_s",
            "buffer_wait_s",
            "startup_delay_s",
            "migration_delay_s",
        ]
        return {
            component: [
                float(completion.get("delay_components_s", {}).get(component, 0.0))
                for completion in completions
            ]
            for component in components
        }

    def _resolve_link(self, src_stage: str, dst_stage: str) -> Tuple[bool, float, float]:
        """Resolve runtime reachability, bandwidth, and latency for a stage pair."""

        if dst_stage not in set(self.worker_pool_manager.ready_service_ids()):
            return False, 0.0, 0.0

        src_node = self.placement.get(src_stage)
        dst_node = self.placement.get(dst_stage)
        if src_node is None or dst_node is None:
            return False, 0.0, 0.0
        if self._is_node_down(src_node) or self._is_node_down(dst_node):
            return False, 0.0, 0.0

        if src_node == dst_node:
            return True, float("inf"), 0.1

        hops = self._path_hops(src_node, dst_node)
        if not hops:
            return False, 0.0, 0.0
        contact_hops = self._contact_overlay_hops(hops)
        if not self._contact_path_open(contact_hops):
            return False, 0.0, 0.0

        path_resources = self._path_resources(src_node, dst_node)
        if path_resources is None:
            return False, 0.0, 0.0

        has_contact_bandwidth = any("contact_bandwidth" in data for _, _, data in contact_hops)
        hop_bandwidths = [float(data.get("bandwidth", 0.0)) for _, _, data in contact_hops]
        bw_mbps = min(hop_bandwidths) if hop_bandwidths else float("inf")
        if not has_contact_bandwidth:
            bw_mbps = min(bw_mbps, float(path_resources.get("bandwidth", 0.0)))
        has_contact_latency = any("contact_latency" in data for _, _, data in contact_hops)
        latency_ms = (
            sum(float(data.get("latency", 0.0)) for _, _, data in contact_hops)
            if has_contact_latency
            else float(path_resources.get("latency", 0.0))
        )
        if bw_mbps <= 0.0:
            return False, 0.0, 0.0
        return True, bw_mbps, latency_ms

    def _contact_overlay_hops(
        self,
        hops: List[Tuple[str, str, Dict[str, Any]]],
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Return route hops with temporary contact state overlaid for runtime transport."""

        overlaid: List[Tuple[str, str, Dict[str, Any]]] = []
        for src, dst, attrs in hops:
            hop_attrs = dict(attrs)
            if "contact_bandwidth" in hop_attrs:
                hop_attrs["bandwidth"] = float(hop_attrs["contact_bandwidth"])
            if "contact_latency" in hop_attrs:
                hop_attrs["latency"] = float(hop_attrs["contact_latency"])
            overlaid.append((src, dst, hop_attrs))
        return overlaid

    def _contact_path_open(self, hops: List[Tuple[str, str, Dict[str, Any]]]) -> bool:
        """Return whether all temporary contact windows on a route are open."""

        for _src, _dst, attrs in hops:
            if "contact_open" in attrs and float(attrs.get("contact_open", 0.0)) <= 0.0:
                return False
        return True

    def _path_resources(self, src_node: str, dst_node: str) -> Optional[Dict[str, Any]]:
        """Return Eclypse aggregate path resources when available."""

        path_resources_fn = getattr(self.infrastructure, "path_resources", None)
        if not callable(path_resources_fn):
            return None

        try:
            return dict(path_resources_fn(src_node, dst_node))
        except Exception:
            return None

    def _path_hops(self, src_node: str, dst_node: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Return the infrastructure hops between two nodes."""

        path_fn = getattr(self.infrastructure, "path", None)
        if not callable(path_fn):
            return []

        path = path_fn(src_node, dst_node)
        if path is None:
            return []
        if isinstance(path, tuple):
            hops = path[0]
        else:
            hops = path
        return list(hops)

    def _invalidate_infrastructure_views(self) -> None:
        """Clear Eclypse cached node-availability and path views after graph mutation."""

        invalidate = getattr(self.infrastructure, "invalidate_views", None)
        if callable(invalidate):
            invalidate()
            return
        for attr in ("_available", "_paths", "_costs"):
            if hasattr(self.infrastructure, attr):
                value = {} if attr in {"_paths", "_costs"} else None
                setattr(self.infrastructure, attr, value)

    def _install_failure_update_policy(self) -> None:
        """Attach the benchmark failure adapter to Eclypse update policies."""

        node_policies = getattr(self.infrastructure, "node_update_policy", None)
        if isinstance(node_policies, list):
            node_policies.append(self._node_failure_policy.apply_nodes)

    def _install_contact_update_policy(self) -> None:
        """Attach the contact schedule adapter to Eclypse update policies."""

        edge_policies = getattr(self.infrastructure, "edge_update_policy", None)
        if isinstance(edge_policies, list):
            edge_policies.append(self._contact_schedule_policy.apply_edges)

    def _evolve_contact_schedule(self, time_s: float) -> Dict[Tuple[str, str], Dict[str, float]]:
        """Apply contact schedule updates through Eclypse graph evolution."""

        self._contact_schedule_policy.set_time(time_s)
        self.infrastructure.evolve()
        return dict(self._contact_schedule_policy.last_updates)

    def _restore_node_failure_availability(self) -> None:
        """Restore node availability before applying this epoch's failures."""

        self._node_failure_policy.restore(self.infrastructure)
        self._invalidate_infrastructure_views()

    def _evolve_node_failure_availability(self) -> None:
        """Apply selected benchmark node failures through Eclypse update policies."""

        self._node_failure_policy.set_failures(
            node_down=self._active_node_failures,
        )
        self.infrastructure.evolve()
        self._node_failure_policy.clear_failures()

    def _latency_to_epochs(self, latency_ms: float) -> int:
        """Convert link latency in milliseconds into epoch delay."""

        latency_s = max(0.0, float(latency_ms) / 1000.0)
        return max(1, int(math.ceil(latency_s / self.step_seconds)))

    def _message_delay_epochs(
        self,
        src_stage: str,
        dst_stage: str,
        task: Task,
        fallback_latency_ms: float,
    ) -> int:
        """Convert Eclypse route cost for one message into an epoch delay."""

        route_cost_s = self._eclypse_route_cost_s(src_stage, dst_stage, task)
        if route_cost_s is None:
            return self._latency_to_epochs(fallback_latency_ms)
        return max(1, int(math.ceil(max(0.0, route_cost_s) / self.step_seconds)))

    def _eclypse_route_cost_s(
        self,
        src_stage: str,
        dst_stage: str,
        task: Task,
    ) -> Optional[float]:
        """Return Eclypse Route.cost() for a placed service interaction."""

        if EclypseRoute is None:
            return None
        src_node = self.placement.get(src_stage)
        dst_node = self.placement.get(dst_stage)
        if src_node is None or dst_node is None:
            return None
        if src_node == dst_node:
            return 0.0

        route_data = self.infrastructure.path(src_node, dst_node)
        if route_data is None:
            return None

        hops, processing_time = route_data
        hops = self._contact_overlay_hops(list(hops))
        if not self._contact_path_open(hops):
            return None
        route = EclypseRoute(
            sender_id=src_stage,
            sender_node_id=src_node,
            recipient_id=dst_stage,
            recipient_node_id=dst_node,
            processing_time=float(processing_time),
            hops=hops,
        )
        message = _SizedRoutePayload(task.size_bytes)
        return float(route.cost(message))

    def _bandwidth_to_bytes(self, bandwidth_mbps: float) -> int:
        """Convert per-second bandwidth into one-epoch byte capacity."""

        if math.isinf(bandwidth_mbps):
            return 10**18
        return max(0, int(float(bandwidth_mbps) * 125_000 * self.step_seconds))

    def _bandwidth_budget_key(self, src_stage: str, dst_stage: str) -> Tuple[Tuple[str, str], ...]:
        """Return the hop key that shares a bandwidth budget."""

        src_node = self.placement.get(src_stage)
        dst_node = self.placement.get(dst_stage)
        if src_node is None or dst_node is None:
            return ((src_stage, dst_stage),)
        if src_node == dst_node:
            return ((src_node, dst_node),)

        hops = self._path_hops(src_node, dst_node)
        if not hops:
            return ((src_node, dst_node),)

        bandwidths = [float(attrs.get("bandwidth", 0.0)) for _src, _dst, attrs in hops]
        positive_bandwidths = [bandwidth for bandwidth in bandwidths if bandwidth > 0.0]
        if not positive_bandwidths:
            return tuple((src, dst) for src, dst, _attrs in hops)

        bottleneck_bw = min(positive_bandwidths)
        bottleneck_hops = [
            (src, dst)
            for src, dst, attrs in hops
            if float(attrs.get("bandwidth", 0.0)) == bottleneck_bw
        ]
        return tuple(bottleneck_hops) if bottleneck_hops else tuple((src, dst) for src, dst, _attrs in hops)

    def _record_traffic(self, src_stage: str, dst_stage: str, size_bytes: int) -> None:
        """Accumulate cross-tier traffic counters for one delivery."""

        src_node = self.placement.get(src_stage)
        dst_node = self.placement.get(dst_stage)
        if src_node is None or dst_node is None or src_node == dst_node:
            return

        hops = self._path_hops(src_node, dst_node)
        if not hops:
            return

        bytes_sent = max(1, int(size_bytes))
        for hop_src, hop_dst, _attrs in hops:
            src_layer = self._node_layer(hop_src)
            dst_layer = self._node_layer(hop_dst)
            link_key = self._link_key(hop_src, hop_dst)
            self._traffic_link_bytes_this_epoch[link_key] = (
                self._traffic_link_bytes_this_epoch.get(link_key, 0) + bytes_sent
            )
            if not src_layer or not dst_layer or src_layer == dst_layer:
                continue
            tier_pair = f"{src_layer}->{dst_layer}"
            self._traffic_bytes_this_epoch[tier_pair] = self._traffic_bytes_this_epoch.get(tier_pair, 0) + bytes_sent

    def _compute_resource_snapshot(self) -> Dict[str, Any]:
        """Compute demand-based node resource utilization for the current epoch."""

        reserved_snapshot = self._compute_reserved_resource_snapshot()
        active_runtime_snapshot = self._compute_active_runtime_resource_snapshot()
        result = dict(active_runtime_snapshot)
        result["reserved"] = reserved_snapshot
        result["active_runtime"] = active_runtime_snapshot
        return result

    def _compute_active_runtime_resource_snapshot(self) -> Dict[str, Any]:
        """Compute queue-weighted active resource utilization for the current epoch."""

        ready_service_ids = set(self.worker_pool_manager.ready_service_ids())
        node_stats: Dict[str, Dict[str, Any]] = {}

        for stage_id, runtime in self.stage_runtimes.items():
            if runtime.role == "source" or stage_id not in ready_service_ids or not runtime.queue:
                continue

            node_id = self.placement.get(stage_id)
            if node_id is None or self._is_node_down(node_id):
                continue

            resource_demand = self._placement_resource_demand(node_id, stage_id)
            if resource_demand is None:
                resource_demand = self._stage_resource_demand(runtime.profile)
            if all(value <= 0.0 for value in resource_demand.values()):
                continue

            stats = node_stats.setdefault(node_id, self._empty_node_resource_stats(node_id))
            for resource, demand in resource_demand.items():
                stats[resource]["demand"] += demand
            stats["stages"][stage_id] = {
                "cpu_demand": resource_demand["cpu"],
                "resource_demand": dict(resource_demand),
                "queue_depth": len(runtime.queue),
            }

        aggregate: Dict[str, Dict[str, float]] = {
            resource: {"demand": 0.0, "capacity": 0.0, "utilization": 0.0, "overcommit": 0.0}
            for resource in RESOURCE_DIMENSIONS
        }
        for stats in node_stats.values():
            for resource in RESOURCE_DIMENSIONS:
                bucket = stats[resource]
                demand = float(bucket.get("demand", 0.0))
                capacity = max(0.0, float(bucket.get("capacity", 0.0)))
                utilization = (demand / capacity) if capacity > 0.0 else (1.0 if demand > 0.0 else 0.0)
                overcommit = max(0.0, demand - capacity)
                bucket["capacity"] = capacity
                bucket["utilization"] = utilization
                bucket["overcommit"] = overcommit
                aggregate[resource]["demand"] += demand
                aggregate[resource]["capacity"] += capacity
                aggregate[resource]["overcommit"] += overcommit

        for resource, bucket in aggregate.items():
            demand = bucket["demand"]
            capacity = bucket["capacity"]
            bucket["utilization"] = (
                demand / capacity
                if capacity > 0.0
                else (1.0 if demand > 0.0 else 0.0)
            )

        for node_id, stats in node_stats.items():
            for stage_id, stage_stats in stats["stages"].items():
                stage_stats["processing_factor"] = self._stage_processing_factor_from_stats(
                    node_id,
                    stage_id,
                    stats,
                )

        result: Dict[str, Any] = {resource: aggregate[resource] for resource in RESOURCE_DIMENSIONS}
        result["nodes"] = node_stats
        return result

    def _compute_reserved_resource_snapshot(self) -> Dict[str, Any]:
        """Compute Eclypse placement-view reserved resource utilization."""

        node_stats: Dict[str, Dict[str, Any]] = {}
        for node_id in self._infrastructure_node_ids():
            stage_demands = self._placement_adapter.demands_for_node(node_id)
            aggregate_demand = self._placement_adapter.reserved_demand_for_node(node_id)
            if not stage_demands and all(value <= 0.0 for value in aggregate_demand.values()):
                continue
            stats = node_stats.setdefault(node_id, self._empty_node_resource_stats(node_id))
            for resource, demand in aggregate_demand.items():
                stats[resource]["demand"] = demand
            for stage_id, resource_demand in sorted(stage_demands.items()):
                stats["stages"][stage_id] = {
                    "cpu_demand": resource_demand["cpu"],
                    "resource_demand": dict(resource_demand),
                    "queue_depth": len(self.stage_runtimes[stage_id].queue)
                    if stage_id in self.stage_runtimes
                    else 0,
                }

        aggregate: Dict[str, Dict[str, float]] = {
            resource: {"demand": 0.0, "capacity": 0.0, "utilization": 0.0, "overcommit": 0.0}
            for resource in RESOURCE_DIMENSIONS
        }
        for stats in node_stats.values():
            for resource in RESOURCE_DIMENSIONS:
                bucket = stats[resource]
                demand = float(bucket.get("demand", 0.0))
                capacity = max(0.0, float(bucket.get("capacity", 0.0)))
                utilization = (demand / capacity) if capacity > 0.0 else (1.0 if demand > 0.0 else 0.0)
                overcommit = max(0.0, demand - capacity)
                bucket["capacity"] = capacity
                bucket["utilization"] = utilization
                bucket["overcommit"] = overcommit
                aggregate[resource]["demand"] += demand
                aggregate[resource]["capacity"] += capacity
                aggregate[resource]["overcommit"] += overcommit

        for resource, bucket in aggregate.items():
            demand = bucket["demand"]
            capacity = bucket["capacity"]
            bucket["utilization"] = (
                demand / capacity
                if capacity > 0.0
                else (1.0 if demand > 0.0 else 0.0)
            )

        for node_id, stats in node_stats.items():
            for stage_id, stage_stats in stats["stages"].items():
                stage_stats["processing_factor"] = self._stage_processing_factor_from_stats(
                    node_id,
                    stage_id,
                    stats,
                )

        result: Dict[str, Any] = {resource: aggregate[resource] for resource in RESOURCE_DIMENSIONS}
        result["nodes"] = node_stats
        return result

    def _placement_resource_demand(
        self,
        node_id: str,
        stage_id: str,
    ) -> Optional[Dict[str, float]]:
        """Return Eclypse placement demand for a runtime stage."""

        return self._placement_adapter.demand_for_stage(node_id, stage_id)

    def _compute_energy_snapshot(self, resource_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Compute configurable per-node and network energy use for the current epoch."""

        by_node: Dict[str, Dict[str, float]] = {}
        compute_total_j = 0.0

        for node_id in self._infrastructure_node_ids():
            node_attrs = self._node_attrs(node_id)
            node_stats = resource_snapshot.get("nodes", {}).get(node_id, {})
            active_resources: Dict[str, float] = {}
            for resource in RESOURCE_DIMENSIONS:
                bucket = node_stats.get(resource, {})
                demand = max(0.0, float(bucket.get("demand", 0.0)))
                capacity = max(0.0, float(bucket.get("capacity", self._node_resource_capacity(node_id, resource))))
                active_resources[resource] = min(demand, capacity) if capacity > 0.0 else 0.0

            idle_power_w = self._float_attr(node_attrs, "idle_power_w", 0.0)
            dynamic_power_w = 0.0
            for resource, active_value in active_resources.items():
                dynamic_power_w += (
                    self._float_attr(node_attrs, f"dynamic_power_w_per_{resource}", 0.0)
                    * active_value
                )
            idle_energy_j = idle_power_w * self.step_seconds
            dynamic_energy_j = dynamic_power_w * self.step_seconds
            energy_j = idle_energy_j + dynamic_energy_j
            compute_total_j += energy_j

            record: Dict[str, float] = {
                "power_w": float(idle_power_w + dynamic_power_w),
                "idle_energy_j": float(idle_energy_j),
                "dynamic_energy_j": float(dynamic_energy_j),
                "energy_j": float(energy_j),
            }
            for resource, active_value in active_resources.items():
                record[f"active_{resource}"] = float(active_value)

            battery_capacity = self._optional_float_attr(node_attrs, "battery_capacity_j")
            if battery_capacity is not None:
                previous_remaining = self._optional_float_attr(node_attrs, "battery_remaining_j")
                if previous_remaining is None:
                    previous_remaining = battery_capacity
                remaining = max(0.0, previous_remaining - energy_j)
                node_attrs["battery_remaining_j"] = remaining
                record["battery_capacity_j"] = float(battery_capacity)
                record["battery_remaining_j"] = float(remaining)

            by_node[node_id] = record

        by_link_or_tier_pair = self._compute_network_energy_by_link()
        network_total_j = sum(float(record["energy_j"]) for record in by_link_or_tier_pair.values())
        total_energy_j = compute_total_j + network_total_j

        return {
            "total_j": float(total_energy_j),
            "compute_total_j": float(compute_total_j),
            "network_total_j": float(network_total_j),
            "by_node": by_node,
            "by_link_or_tier_pair": by_link_or_tier_pair,
        }

    def _compute_reliability_snapshot(
        self,
        *,
        generated_count: int,
        completed_count: int,
        buffers_snapshot: Dict[str, Any],
        failure_failed_tasks: int = 0,
    ) -> Dict[str, Any]:
        """Compute per-epoch task success/failure signals."""

        cumulative = buffers_snapshot.get("cumulative", {})
        dropped = int(cumulative.get("items_dropped", 0))
        expired = int(cumulative.get("items_expired", 0))
        buffer_failed = dropped + expired
        failed = buffer_failed + int(failure_failed_tasks)
        generated = int(generated_count)
        completed = int(completed_count)
        pending = max(0, generated - completed - failed)
        resolved = min(generated, completed + failed)
        return {
            "generated_tasks": generated,
            "completed_tasks": completed,
            "pending_tasks": pending,
            "resolved_tasks": resolved,
            "failed_tasks": failed,
            "buffer_failed_tasks": buffer_failed,
            "failure_event_failed_tasks": int(failure_failed_tasks),
            "success_rate": (completed / generated) if generated > 0 else 0.0,
            "resolved_rate": (resolved / generated) if generated > 0 else 0.0,
            "pending_rate": (pending / generated) if generated > 0 else 0.0,
            "failure_rate": (failed / generated) if generated > 0 else 0.0,
        }

    def _stage_processing_factor(
        self,
        stage_id: str,
        resource_snapshot: Optional[Dict[str, Any]],
    ) -> float:
        """Return the node-level multi-resource sharing factor for a stage."""

        if resource_snapshot is None:
            return 1.0

        node_id = self.placement.get(stage_id)
        if node_id is None:
            return 1.0

        node_stats = resource_snapshot.get("nodes", {}).get(node_id)
        if not node_stats:
            return 1.0

        return self._stage_processing_factor_from_stats(node_id, stage_id, node_stats)

    def _stage_processing_factor_from_stats(
        self,
        node_id: str,
        stage_id: str,
        node_stats: Dict[str, Any],
    ) -> float:
        """Return the minimum effective share across Eclypse resource dimensions."""

        stage_stats = node_stats.get("stages", {}).get(stage_id, {})
        resource_demand = stage_stats.get("resource_demand", {})
        factors: List[float] = []

        for resource in RESOURCE_DIMENSIONS:
            stage_demand = max(0.0, float(resource_demand.get(resource, 0.0)))
            if stage_demand <= 0.0:
                continue

            bucket = node_stats.get(resource, {})
            node_demand = max(0.0, float(bucket.get("demand", 0.0)))
            capacity = max(
                0.0,
                float(bucket.get("capacity", self._node_resource_capacity(node_id, resource))),
            )
            if capacity <= 0.0:
                factors.append(0.0)
                continue
            if node_demand <= 0.0:
                factors.append(1.0)
                continue
            factors.append(min(1.0, capacity / node_demand))

        if not factors:
            return 1.0
        return min(factors)

    def _stage_cpu_demand(self, profile: Dict[str, Any]) -> float:
        """Return the CPU demand declared by a stage profile."""

        return self._stage_resource_demand(profile)["cpu"]

    def _stage_resource_demand(self, profile: Dict[str, Any]) -> Dict[str, float]:
        """Return per-resource demand declared by a stage profile."""

        resources = profile.get("resources", {})
        result: Dict[str, float] = {}
        for resource in RESOURCE_DIMENSIONS:
            default = 1.0 if resource == "cpu" else 0.0
            if isinstance(resources, dict):
                raw_value = resources.get(resource, profile.get(resource, default))
            else:
                raw_value = profile.get(resource, default)
            try:
                result[resource] = max(0.0, float(raw_value))
            except (TypeError, ValueError):
                result[resource] = 0.0
        return result

    def _empty_node_resource_stats(self, node_id: str) -> Dict[str, Any]:
        """Return empty per-resource accounting buckets for one node."""

        return {
            **{
                resource: {
                    "demand": 0.0,
                    "capacity": self._node_resource_capacity(node_id, resource),
                    "utilization": 0.0,
                    "overcommit": 0.0,
                }
                for resource in RESOURCE_DIMENSIONS
            },
            "stages": {},
        }

    def _node_cpu_capacity(self, node_id: str) -> float:
        """Return the CPU capacity declared by an infrastructure node."""

        return self._node_resource_capacity(node_id, "cpu")

    def _node_resource_capacity(self, node_id: str, resource: str) -> float:
        """Return the resource capacity declared by an infrastructure node."""

        default = 1.0 if resource == "cpu" else 0.0
        return max(0.0, self._float_attr(self._node_attrs(node_id), resource, default))

    def _infrastructure_node_ids(self) -> List[str]:
        """Return all provisioned infrastructure node IDs."""

        nodes = self.infrastructure.nodes
        if hasattr(nodes, "keys"):
            return list(nodes.keys())
        return list(nodes)

    def _node_attrs(self, node_id: str) -> Dict[str, Any]:
        """Return a mutable provisioned-node attribute mapping."""

        nodes = self.infrastructure.nodes
        if hasattr(nodes, "get"):
            attrs = nodes.get(node_id, {})
            return attrs if isinstance(attrs, dict) else dict(attrs)
        try:
            attrs = nodes[node_id]
            return attrs if isinstance(attrs, dict) else dict(attrs)
        except Exception:
            return {}

    def _float_attr(self, attrs: Dict[str, Any], key: str, default: float) -> float:
        """Read a numeric node attribute with a robust fallback."""

        try:
            return float(attrs.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _optional_float_attr(self, attrs: Dict[str, Any], key: str) -> Optional[float]:
        """Read an optional numeric node attribute."""

        if key not in attrs:
            return None
        try:
            return float(attrs[key])
        except (TypeError, ValueError):
            return None

    def _apply_failure_model(self, epoch: int) -> Dict[str, Any]:
        """Select node failures for one epoch."""

        self._active_node_failures = set()
        mode = str(self._failure_cfg.get("mode", "disabled")).lower()
        if mode in {"", "disabled", "none", "off"}:
            return {"node_down": [], "failed_tasks": 0}

        if mode in {"deterministic", "events"}:
            for event in self._failure_cfg.get("events", []):
                if int(event.get("epoch", -1)) != int(epoch):
                    continue
                self._active_node_failures.update(str(node_id) for node_id in event.get("node_down", []))
        elif mode == "bernoulli":
            node_probability = self._clamped_probability(
                self._failure_cfg.get("node_failure_probability", 0.0)
            )
            for node_id in self._infrastructure_node_ids():
                if self._failure_rng.random() < node_probability:
                    self._active_node_failures.add(str(node_id))
        else:
            raise ValueError(f"Unsupported reliability.failures mode: {mode}")

        self._evolve_node_failure_availability()

        return {
            "node_down": sorted(self._active_node_failures),
            "failed_tasks": 0,
        }

    def _clamped_probability(self, value: Any) -> float:
        """Read a probability and clamp it into [0, 1]."""

        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _is_node_down(self, node_id: str) -> bool:
        """Return whether the infrastructure node is failed this epoch."""

        return str(node_id) in self._active_node_failures

    def _link_key(self, src: str, dst: str) -> str:
        """Return the stable directed link key used for logs and failure events."""

        return f"{src}->{dst}"

    def _compute_network_energy_by_link(self) -> Dict[str, Dict[str, float]]:
        """Compute network transport energy from per-link byte counters."""

        by_link: Dict[str, Dict[str, float]] = {}
        for link_key, bytes_sent in sorted(self._traffic_link_bytes_this_epoch.items()):
            if "->" not in link_key:
                continue
            src, dst = link_key.split("->", maxsplit=1)
            attrs = self._edge_attrs(src, dst)
            energy_j_per_byte = self._float_attr(attrs, "energy_j_per_byte", 0.0)
            energy_j = max(0, int(bytes_sent)) * max(0.0, energy_j_per_byte)
            by_link[link_key] = {
                "bytes": int(bytes_sent),
                "energy_j": float(energy_j),
            }
        return by_link

    def _edge_attrs(self, src: str, dst: str) -> Dict[str, Any]:
        """Return edge attributes for a directed infrastructure link."""

        edges = getattr(self.infrastructure, "edges", {})
        if hasattr(edges, "get"):
            return dict(edges.get((src, dst), {}))
        try:
            return dict(edges[src, dst])
        except Exception:
            return {}

    def _compute_controller_reward(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """Compute a lightweight reward for policy-trajectory logging."""

        latencies = [float(value) for value in outcome.get("completions", {}).get("latencies_s", [])]
        latency_component = -sum(latencies) / len(latencies) if latencies else 0.0
        slo_component = -float(outcome.get("completions", {}).get("slo_violation_count", 0))
        energy_component = -float(outcome.get("energy", {}).get("total_j", 0.0))
        reliability_component = -float(outcome.get("reliability", {}).get("failure_rate", 0.0))
        churn = outcome.get("churn", {})
        churn_component = -(
            float(churn.get("migrations", 0.0))
            + float(churn.get("delta_k", 0.0))
        )
        reward = latency_component + slo_component + energy_component + reliability_component + churn_component
        return {
            "reward": float(reward),
            "components": {
                "latency": float(latency_component),
                "slo": float(slo_component),
                "energy": float(energy_component),
                "reliability": float(reliability_component),
                "churn": float(churn_component),
            },
        }

    def _node_layer(self, node_id: str) -> str:
        """Return the normalized Eclypse symbolic placement layer for a node."""

        nodes = self.infrastructure.nodes
        attrs = nodes.get(node_id, {}) if hasattr(nodes, "get") else nodes[node_id]
        placement_layers = dict(attrs).get("placement_layers", [])
        if isinstance(placement_layers, list) and len(placement_layers) == 1:
            return str(placement_layers[0]).lower()
        return ""

    def _sample_priority(self, profile: Dict[str, Any]) -> str:
        """Sample a task priority from the stage profile."""

        p_high = min(1.0, max(0.0, float(profile.get("priority_prob_high", 0.1))))
        return "high" if self.rng.random() < p_high else "low"

    def _compute_aoi_proxy(self, epoch: int) -> float:
        """Compute the age-of-information proxy at epoch end."""

        now_s = (epoch + 1) * self.step_seconds
        if self.last_delivered_created_time_s is None:
            return now_s
        return max(0.0, now_s - self.last_delivered_created_time_s)

    def _aggregate_buffer_snapshots(self) -> Dict[str, Any]:
        """Aggregate per-stage and cumulative buffer statistics."""

        per_stage: Dict[str, Dict[str, Any]] = {}
        cumulative = {
            "items_buffered": 0,
            "items_transmitted": 0,
            "items_dropped": 0,
            "items_expired": 0,
            "bytes_buffered": 0,
            "bytes_transmitted": 0,
            "bytes_dropped": 0,
            "bytes_expired": 0,
            "priority": {
                priority.name: {
                    "items_buffered": 0,
                    "items_transmitted": 0,
                    "items_dropped": 0,
                    "items_expired": 0,
                    "bytes_buffered": 0,
                    "bytes_transmitted": 0,
                    "bytes_dropped": 0,
                    "bytes_expired": 0,
                }
                for priority in BufferPriority
            },
        }

        total_items = 0
        total_bytes = 0

        for stage_id, runtime in self.stage_runtimes.items():
            stage_items = 0
            stage_bytes = 0
            stage_ttl_pressure = 0.0
            stage_queue_sizes: Dict[str, int] = {}

            for target, buffer in runtime.buffers.items():
                stats = buffer.get_stats()
                stage_items += buffer.size
                stage_bytes += buffer.size_bytes
                stage_ttl_pressure = max(stage_ttl_pressure, self._buffer_ttl_pressure(buffer))
                stage_queue_sizes[target] = buffer.size

                cumulative["items_buffered"] += int(stats.get("items_buffered", 0))
                cumulative["items_transmitted"] += int(stats.get("items_transmitted", 0))
                cumulative["items_dropped"] += int(stats.get("items_dropped", 0))
                cumulative["items_expired"] += int(stats.get("items_expired", 0))
                cumulative["bytes_buffered"] += int(stats.get("bytes_buffered", 0))
                cumulative["bytes_transmitted"] += int(stats.get("bytes_transmitted", 0))
                cumulative["bytes_dropped"] += int(stats.get("bytes_dropped", 0))
                cumulative["bytes_expired"] += int(stats.get("bytes_expired", 0))

                for priority, priority_stats in stats.get("priority", {}).items():
                    for key in cumulative["priority"][priority]:
                        cumulative["priority"][priority][key] += int(priority_stats.get(key, 0))

            per_stage[stage_id] = {
                "items": stage_items,
                "bytes": stage_bytes,
                "ttl_pressure": stage_ttl_pressure,
                "queue_sizes": stage_queue_sizes,
            }
            total_items += stage_items
            total_bytes += stage_bytes

        return {
            "items": total_items,
            "bytes": total_bytes,
            "per_stage": per_stage,
            "cumulative": cumulative,
        }

    def _buffer_snapshot(self, runtime: StageRuntime) -> Tuple[int, int, float]:
        """Summarize buffered items, bytes, and TTL pressure for a stage."""

        items = 0
        bytes_val = 0
        ttl_pressure = 0.0
        for buffer in runtime.buffers.values():
            items += buffer.size
            bytes_val += buffer.size_bytes
            ttl_pressure = max(ttl_pressure, self._buffer_ttl_pressure(buffer))
        return items, bytes_val, ttl_pressure

    def _pool_buffer_snapshot(self, pool_stage: str) -> Tuple[int, float, float]:
        """Summarize buffers targeting workers in a pooled stage."""

        pool_workers = set(self.worker_pool_manager.workers_for_stage(pool_stage))
        items = 0
        oldest_age_s = 0.0
        ttl_pressure = 0.0
        for runtime in self.stage_runtimes.values():
            for target_id, buffer in runtime.buffers.items():
                if target_id not in pool_workers:
                    continue
                items += buffer.size
                oldest_age_s = max(oldest_age_s, self._buffer_oldest_age(buffer))
                ttl_pressure = max(ttl_pressure, self._buffer_ttl_pressure(buffer))
        return items, oldest_age_s, ttl_pressure

    def _buffer_oldest_age(self, buffer: StoreForwardBuffer) -> float:
        """Return the oldest buffered item age in seconds."""

        best = 0.0
        for queue in buffer._queues.values():  # pylint: disable=protected-access
            for item in queue:
                best = max(best, item.age)
        return best

    def _buffer_ttl_pressure(self, buffer: StoreForwardBuffer) -> float:
        """Return the highest age-to-TTL ratio in a buffer."""

        best = 0.0
        for queue in buffer._queues.values():  # pylint: disable=protected-access
            for item in queue:
                if item.ttl > 0:
                    best = max(best, item.age / item.ttl)
        return best

    def _topological_stage_order(self) -> List[str]:
        """Return a topological order for the application graph."""

        indeg: Dict[str, int] = {stage_id: 0 for stage_id in self.application.nodes}
        for src, dst in self.application.edges:
            indeg[dst] += 1

        queue = deque(sorted([stage for stage, deg in indeg.items() if deg == 0]))
        order: List[str] = []
        outgoing: Dict[str, List[str]] = {stage_id: [] for stage_id in self.application.nodes}
        for src, dst in self.application.edges:
            outgoing[src].append(dst)

        while queue:
            node = queue.popleft()
            order.append(node)
            for nxt in outgoing[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.application.nodes):
            return list(self.application.nodes)
        return order

    def _oldest_age(self, q: Deque[Task], now_s: float) -> float:
        """Return the age of the oldest queued task."""

        if not q:
            return 0.0
        oldest = min(task.created_time_s for task in q)
        return max(0.0, now_s - oldest)

    def _buffer_priority(self, priority: str) -> BufferPriority:
        """Map a task priority label to a buffer priority enum."""

        normalized = priority.lower()
        if normalized == "critical":
            return BufferPriority.CRITICAL
        if normalized == "high":
            return BufferPriority.HIGH
        if normalized == "low":
            return BufferPriority.LOW
        return BufferPriority.NORMAL


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load a benchmark configuration file as a mapping."""

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("Benchmark config must be a YAML mapping")
    return data


def run_benchmark(
    config_path: Path,
    controller_name: str,
    seed: int,
    out_root: Path,
) -> Dict[str, Any]:
    """Run a benchmark from disk-backed configuration."""

    config = load_config(config_path)
    runner = ContinuumBenchRunner(
        config=config,
        controller_name=controller_name,
        seed=seed,
        out_root=Path(out_root),
    )
    return runner.run()


def summarize_run(run_dir: Path) -> Dict[str, Any]:
    """Recompute and write the summary for an existing run directory."""

    run_path = Path(run_dir)
    metadata_path = run_path / "metadata.json"
    epochs_path = run_path / "epochs.jsonl"

    if not metadata_path.exists() or not epochs_path.exists():
        raise FileNotFoundError(
            f"Run directory must contain metadata.json and epochs.jsonl: {run_path}"
        )

    with metadata_path.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)

    records: List[Dict[str, Any]] = []
    with epochs_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    summary = summarize_epochs(metadata, records)

    with (run_path / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)

    return summary


__all__ = [
    "ContinuumBenchRunner",
    "load_config",
    "run_benchmark",
    "summarize_run",
]
