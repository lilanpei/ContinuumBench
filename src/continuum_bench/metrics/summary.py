from __future__ import annotations

from typing import Any, Dict, Iterable, List

_DEFAULT_FLAT_PRICE = 1.0


def summarize_epochs(metadata: Dict[str, Any], epoch_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-epoch records into summary metrics."""

    if not epoch_records:
        return {
            "metadata": metadata,
            "totals": {},
            "latency_s": {},
            "qos": {},
            "traffic": {},
            "freshness": {},
            "resource_utilization": {
                "cpu_mean": 0.0,
                "cpu_peak": 0.0,
                "cpu_overcommit_mean": 0.0,
                "cpu_overcommit_peak": 0.0,
                "ram_mean": 0.0,
                "ram_peak": 0.0,
                "ram_overcommit_mean": 0.0,
                "ram_overcommit_peak": 0.0,
                "storage_mean": 0.0,
                "storage_peak": 0.0,
                "storage_overcommit_mean": 0.0,
                "storage_overcommit_peak": 0.0,
            },
            "energy": {
                "total_j": 0.0,
                "compute_total_j": 0.0,
                "network_total_j": 0.0,
                "mean_power_w": 0.0,
                "battery_remaining_j_by_node": {},
            },
            "cost": {"total_provisioned_worker_seconds": 0.0, "mean_active_workers": 0.0,
                     "flat_price_per_worker_second": float(metadata.get("cost_flat_price_per_worker_second", _DEFAULT_FLAT_PRICE))},
            "failures": {
                "node_down_events": 0,
                "failed_tasks": 0,
            },
            "reliability": {
                "generated_tasks": 0,
                "completed_tasks": 0,
                "pending_tasks": 0,
                "resolved_tasks": 0,
                "failed_tasks": 0,
                "buffer_failed_tasks": 0,
                "failure_event_failed_tasks": 0,
                "success_rate": 0.0,
                "resolved_rate": 0.0,
                "pending_rate": 0.0,
                "failure_rate": 0.0,
                "drop_rate": 0.0,
                "expire_rate": 0.0,
            },
            "controller_reward": {
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
            },
            "store_forward": {},
            "contact": {
                "active_link_ratio_mean": 0.0,
                "active_links_mean": 0.0,
                "total_links_mean": 0.0,
                "closed_link_epochs": 0,
            },
            "stability": {
                "startup_delay_s_total": 0.0,
                "startup_delay_s_mean": 0.0,
                "migration_delay_s_total": 0.0,
                "migration_delay_s_mean": 0.0,
            },
            "controller_overhead_ms": {},
            "delay_breakdown_s": {},
        }

    latencies: List[float] = []
    ages_at_delivery: List[float] = []
    generated_count = 0
    throughput_count = 0
    deadline_miss_count = 0
    slo_violation_count = 0
    slo_eligible_count = 0
    decision_times_ms: List[float] = []
    delay_components: Dict[str, List[float]] = {
        "queue_wait_s": [],
        "processing_s": [],
        "transport_s": [],
        "buffer_wait_s": [],
    }
    aoi_proxy_series: List[float] = []
    qos_per_priority: Dict[str, Dict[str, int]] = {}
    traffic_by_tier_pair: Dict[str, int] = {}
    cross_tier_bytes_total = 0
    cpu_utilization_series: List[float] = []
    cpu_overcommit_series: List[float] = []
    ram_utilization_series: List[float] = []
    ram_overcommit_series: List[float] = []
    storage_utilization_series: List[float] = []
    storage_overcommit_series: List[float] = []
    energy_total_j = 0.0
    compute_energy_total_j = 0.0
    network_energy_total_j = 0.0
    battery_remaining_by_node: Dict[str, float] = {}
    node_down_events = 0
    failure_event_failed_tasks = 0
    controller_rewards: List[float] = []

    scaling_delta_series: List[float] = []
    migrations_series: List[float] = []
    startup_delay_series: List[float] = []
    migration_delay_series: List[float] = []

    buffered_items_series: List[float] = []
    buffered_bytes_series: List[float] = []
    active_link_ratio_series: List[float] = []
    active_links_series: List[float] = []
    total_links_series: List[float] = []
    closed_link_epochs = 0

    for record in epoch_records:
        completions = record.get("completions", {})
        epoch_latencies = [float(value) for value in completions.get("latencies_s", [])]
        epoch_ages = [float(value) for value in completions.get("age_at_delivery_s", epoch_latencies)]
        completion_delay_components = completions.get("delay_components_s", {})
        if isinstance(completion_delay_components, dict):
            for component, values in completion_delay_components.items():
                if component not in delay_components:
                    delay_components[component] = []
                delay_components[component].extend(float(value) for value in values)

        generated_count += int(completions.get("generated", 0))
        latencies.extend(epoch_latencies)
        ages_at_delivery.extend(epoch_ages)
        throughput_count += int(completions.get("count", 0))
        deadline_miss_count += int(completions.get("deadline_miss_count", 0))
        slo_violation_count += int(completions.get("slo_violation_count", 0))
        slo_eligible_count += int(completions.get("slo_eligible_count", 0))

        for priority, count in completions.get("generated_by_priority", {}).items():
            stats = qos_per_priority.setdefault(
                priority,
                {
                    "generated": 0,
                    "completed": 0,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 0,
                    "slo_eligible_count": 0,
                },
            )
            stats["generated"] += int(count)

        for priority, count in completions.get("slo_eligible_by_priority", {}).items():
            stats = qos_per_priority.setdefault(
                priority,
                {
                    "generated": 0,
                    "completed": 0,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 0,
                    "slo_eligible_count": 0,
                },
            )
            stats["slo_eligible_count"] += int(count)

        for priority, priority_stats in completions.get("per_priority", {}).items():
            stats = qos_per_priority.setdefault(
                priority,
                {
                    "generated": 0,
                    "completed": 0,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 0,
                    "slo_eligible_count": 0,
                },
            )
            stats["completed"] += int(priority_stats.get("completed", 0))
            stats["deadline_miss_count"] += int(priority_stats.get("deadline_miss_count", 0))
            stats["slo_violation_count"] += int(priority_stats.get("slo_violation_count", 0))

        traffic = record.get("traffic", {})
        cross_tier_bytes_total += int(traffic.get("cross_tier_bytes", 0))
        for tier_pair, bytes_count in traffic.get("by_tier_pair", {}).items():
            traffic_by_tier_pair[tier_pair] = traffic_by_tier_pair.get(tier_pair, 0) + int(bytes_count)

        resources = record.get("resources", {})
        cpu = resources.get("cpu", {})
        cpu_utilization_series.append(float(cpu.get("utilization", 0.0)))
        cpu_overcommit_series.append(float(cpu.get("overcommit", 0.0)))
        ram = resources.get("ram", {})
        ram_utilization_series.append(float(ram.get("utilization", 0.0)))
        ram_overcommit_series.append(float(ram.get("overcommit", 0.0)))
        storage = resources.get("storage", {})
        storage_utilization_series.append(float(storage.get("utilization", 0.0)))
        storage_overcommit_series.append(float(storage.get("overcommit", 0.0)))

        energy = record.get("energy", {})
        energy_total_j += float(energy.get("total_j", 0.0))
        compute_energy_total_j += float(energy.get("compute_total_j", energy.get("total_j", 0.0)))
        network_energy_total_j += float(energy.get("network_total_j", 0.0))
        for node_id, node_energy in energy.get("by_node", {}).items():
            if "battery_remaining_j" in node_energy:
                battery_remaining_by_node[node_id] = float(node_energy["battery_remaining_j"])

        failures = record.get("failures", {})
        node_down_events += len(failures.get("node_down", []))
        failure_event_failed_tasks += int(failures.get("failed_tasks", 0))

        if "controller_reward" in record:
            controller_rewards.append(float(record.get("controller_reward", {}).get("reward", 0.0)))

        decision_times_ms.append(float(record.get("decision_time_ms", 0.0)))
        aoi_proxy_series.append(float(record.get("freshness", {}).get("aoi_proxy_s", 0.0)))

        churn = record.get("churn", {})
        scaling_delta_series.append(abs(float(churn.get("delta_k", 0.0))))
        migrations_series.append(float(churn.get("migrations", 0.0)))
        startup_delay_series.append(float(churn.get("startup_delay_s", 0.0)))
        migration_delay_series.append(float(churn.get("migration_delay_s", 0.0)))

        buffers = record.get("buffers", {})
        buffered_items_series.append(float(buffers.get("items", 0.0)))
        buffered_bytes_series.append(float(buffers.get("bytes", 0.0)))

        contact = record.get("contact_state", {})
        active_links = float(contact.get("active_links", 0.0))
        total_links = float(contact.get("total_links", 0.0))
        active_links_series.append(active_links)
        total_links_series.append(total_links)
        if total_links > 0.0:
            active_link_ratio = active_links / total_links
            active_link_ratio_series.append(active_link_ratio)
            if active_links < total_links:
                closed_link_epochs += 1

    last_record = epoch_records[-1]
    cumulative = last_record.get("buffers", {}).get("cumulative", {})

    step_seconds = float(metadata.get("step_seconds", 1.0))
    epoch_count = len(epoch_records)
    duration_s = max(1e-9, epoch_count * step_seconds)

    flat_price = float(metadata.get("cost_flat_price_per_worker_second", _DEFAULT_FLAT_PRICE))
    active_sums = [
        sum(int(v) for v in rec.get("active_workers", {}).values())
        for rec in epoch_records
    ]
    cost_block = {
        "total_provisioned_worker_seconds": sum(active_sums) * step_seconds * flat_price,
        "mean_active_workers": sum(active_sums) / len(active_sums) if active_sums else 0.0,
        "flat_price_per_worker_second": flat_price,
        **_tier_priced_cost(metadata, epoch_records, step_seconds),
    }

    drops = int(cumulative.get("items_dropped", 0))
    expires = int(cumulative.get("items_expired", 0))
    buffered = int(cumulative.get("items_buffered", 0))
    buffer_failed_tasks = drops + expires
    failed_tasks = buffer_failed_tasks + failure_event_failed_tasks
    pending_tasks = max(0, generated_count - throughput_count - failed_tasks)
    resolved_tasks = min(generated_count, throughput_count + failed_tasks)

    return {
        "metadata": metadata,
        "totals": {
            "epochs": epoch_count,
            "duration_s": duration_s,
            "generated_tasks": generated_count,
            "completed_tasks": throughput_count,
            "completion_ratio": (throughput_count / generated_count) if generated_count > 0 else 0.0,
            "pending_tasks": pending_tasks,
            "pending_rate": (pending_tasks / generated_count) if generated_count > 0 else 0.0,
            "resolved_rate": (resolved_tasks / generated_count) if generated_count > 0 else 0.0,
            "throughput_tasks_per_s": throughput_count / duration_s,
        },
        "latency_s": {
            "count": len(latencies),
            "min": min(latencies) if latencies else 0.0,
            "p50": _percentile(latencies, 50.0),
            "p95": _percentile(latencies, 95.0),
            "p99": _percentile(latencies, 99.0),
            "mean": _mean(latencies),
        },
        "qos": {
            "deadline_miss_count": deadline_miss_count,
            "slo_violation_count": slo_violation_count,
            "slo_eligible_count": slo_eligible_count,
            "deadline_miss_rate": (deadline_miss_count / throughput_count) if throughput_count > 0 else 0.0,
            "slo_violation_rate": (slo_violation_count / slo_eligible_count) if slo_eligible_count > 0 else 0.0,
            "per_priority": qos_per_priority,
            "on_time_rate_by_priority": _on_time_rates(qos_per_priority),
        },
        "traffic": {
            "cross_tier_bytes_total": cross_tier_bytes_total,
            "by_tier_pair": traffic_by_tier_pair,
        },
        "freshness": {
            "age_at_delivery_p50": _percentile(ages_at_delivery, 50.0),
            "age_at_delivery_p95": _percentile(ages_at_delivery, 95.0),
            "age_at_delivery_p99": _percentile(ages_at_delivery, 99.0),
            "aoi_proxy_mean": _mean(aoi_proxy_series),
            "aoi_proxy_p95": _percentile(aoi_proxy_series, 95.0),
        },
        "resource_utilization": {
            "cpu_mean": _mean(cpu_utilization_series),
            "cpu_peak": max(cpu_utilization_series) if cpu_utilization_series else 0.0,
            "cpu_overcommit_mean": _mean(cpu_overcommit_series),
            "cpu_overcommit_peak": max(cpu_overcommit_series) if cpu_overcommit_series else 0.0,
            "ram_mean": _mean(ram_utilization_series),
            "ram_peak": max(ram_utilization_series) if ram_utilization_series else 0.0,
            "ram_overcommit_mean": _mean(ram_overcommit_series),
            "ram_overcommit_peak": max(ram_overcommit_series) if ram_overcommit_series else 0.0,
            "storage_mean": _mean(storage_utilization_series),
            "storage_peak": max(storage_utilization_series) if storage_utilization_series else 0.0,
            "storage_overcommit_mean": _mean(storage_overcommit_series),
            "storage_overcommit_peak": max(storage_overcommit_series) if storage_overcommit_series else 0.0,
        },
        "energy": {
            "total_j": float(energy_total_j),
            "compute_total_j": float(compute_energy_total_j),
            "network_total_j": float(network_energy_total_j),
            "mean_power_w": energy_total_j / duration_s,
            "battery_remaining_j_by_node": battery_remaining_by_node,
        },
        "cost": cost_block,
        "failures": {
            "node_down_events": int(node_down_events),
            "failed_tasks": int(failure_event_failed_tasks),
        },
        "reliability": {
            "generated_tasks": generated_count,
            "completed_tasks": throughput_count,
            "pending_tasks": pending_tasks,
            "resolved_tasks": resolved_tasks,
            "failed_tasks": failed_tasks,
            "buffer_failed_tasks": buffer_failed_tasks,
            "failure_event_failed_tasks": failure_event_failed_tasks,
            "success_rate": (throughput_count / generated_count) if generated_count > 0 else 0.0,
            "resolved_rate": (resolved_tasks / generated_count) if generated_count > 0 else 0.0,
            "pending_rate": (pending_tasks / generated_count) if generated_count > 0 else 0.0,
            "failure_rate": (failed_tasks / generated_count) if generated_count > 0 else 0.0,
            "drop_rate": (drops / buffered) if buffered > 0 else 0.0,
            "expire_rate": (expires / buffered) if buffered > 0 else 0.0,
        },
        "controller_reward": {
            "mean": _mean(controller_rewards),
            "min": min(controller_rewards) if controller_rewards else 0.0,
            "max": max(controller_rewards) if controller_rewards else 0.0,
        },
        "store_forward": {
            "buffered_items_peak": max(buffered_items_series) if buffered_items_series else 0.0,
            "buffered_bytes_peak": max(buffered_bytes_series) if buffered_bytes_series else 0.0,
            "items_buffered_total": buffered,
            "items_transmitted_total": int(cumulative.get("items_transmitted", 0)),
            "items_dropped_total": drops,
            "items_expired_total": expires,
            "bytes_buffered_total": int(cumulative.get("bytes_buffered", 0)),
            "bytes_transmitted_total": int(cumulative.get("bytes_transmitted", 0)),
            "bytes_dropped_total": int(cumulative.get("bytes_dropped", 0)),
            "bytes_expired_total": int(cumulative.get("bytes_expired", 0)),
            "drop_rate": (drops / buffered) if buffered > 0 else 0.0,
            "expire_rate": (expires / buffered) if buffered > 0 else 0.0,
            "per_priority": cumulative.get("priority", {}),
        },
        "contact": {
            "active_link_ratio_mean": _mean(active_link_ratio_series),
            "active_links_mean": _mean(active_links_series),
            "total_links_mean": _mean(total_links_series),
            "closed_link_epochs": int(closed_link_epochs),
        },
        "stability": {
            "scaling_delta_abs_total": float(sum(scaling_delta_series)),
            "scaling_delta_abs_mean": _mean(scaling_delta_series),
            "migrations_total": float(sum(migrations_series)),
            "migrations_per_epoch": _mean(migrations_series),
            "startup_delay_s_total": float(sum(startup_delay_series)),
            "startup_delay_s_mean": _mean(startup_delay_series),
            "migration_delay_s_total": float(sum(migration_delay_series)),
            "migration_delay_s_mean": _mean(migration_delay_series),
        },
        "controller_overhead_ms": {
            "p50": _percentile(decision_times_ms, 50.0),
            "p95": _percentile(decision_times_ms, 95.0),
            "mean": _mean(decision_times_ms),
        },
        "delay_breakdown_s": {
            **{
                component: {
                    "mean": _mean(values),
                    "p95": _percentile(values, 95.0),
                }
                for component, values in delay_components.items()
            },
            "startup_delay_s": {
                "mean": _mean(startup_delay_series),
                "p95": _percentile(startup_delay_series, 95.0),
            },
            "migration_delay_s": {
                "mean": _mean(migration_delay_series),
                "p95": _percentile(migration_delay_series, 95.0),
            },
        },
    }


def _tier_priced_cost(
    metadata: Dict[str, Any],
    epoch_records: List[Dict[str, Any]],
    step_seconds: float,
) -> Dict[str, Any]:
    """Worker-seconds by hosting tier and their tier-priced total.

    Uses the per-epoch placement of each pool's workers (only active workers are placed) and
    the run's node->tier map. Tiers without a configured price cost the flat price; runs
    without the metadata (older runs) report empty tier blocks and a total of 0.
    """

    node_tiers = dict(metadata.get("node_tiers", {}) or {})
    worker_pools = dict(metadata.get("worker_pools", {}) or {})
    prices = {str(k): float(v) for k, v in (metadata.get("cost_price_per_tier", {}) or {}).items()}
    flat_price = float(metadata.get("cost_flat_price_per_worker_second", _DEFAULT_FLAT_PRICE))
    workers = {worker_id for pool in worker_pools.values() for worker_id in pool}
    seconds_by_tier: Dict[str, float] = {}
    if node_tiers and workers:
        for record in epoch_records:
            for service_id, node_id in (record.get("placement", {}) or {}).items():
                if service_id in workers:
                    tier = str(node_tiers.get(str(node_id), "")) or "unknown"
                    seconds_by_tier[tier] = seconds_by_tier.get(tier, 0.0) + step_seconds
    total = sum(seconds * prices.get(tier, flat_price) for tier, seconds in seconds_by_tier.items())
    return {
        "worker_seconds_by_tier": seconds_by_tier,
        "price_per_tier": prices,
        "total_tier_priced_worker_seconds": total,
    }


def _on_time_rates(per_priority: Dict[str, Dict[str, int]]) -> Dict[str, float]:
    """Per-class on-time rate over SLO-eligible work, under per-class deadlines."""

    out: Dict[str, float] = {}
    for priority, stats in per_priority.items():
        eligible = int(stats.get("slo_eligible_count", 0))
        violations = int(stats.get("slo_violation_count", 0))
        out[priority] = (max(0, eligible - violations) / eligible) if eligible > 0 else 0.0
    return out


def _mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of the values."""

    numbers = list(values)
    if not numbers:
        return 0.0
    return float(sum(numbers) / len(numbers))


def _percentile(values: List[float], percentile: float) -> float:
    """Return a linearly interpolated percentile."""

    if not values:
        return 0.0

    numbers = sorted(float(value) for value in values)
    if len(numbers) == 1:
        return numbers[0]

    rank = (len(numbers) - 1) * (float(percentile) / 100.0)
    low = int(rank)
    high = min(low + 1, len(numbers) - 1)
    fraction = rank - low
    return numbers[low] * (1.0 - fraction) + numbers[high] * fraction
