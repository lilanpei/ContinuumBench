# Calibration Profiles

## Purpose

This directory holds the public calibration hook layer for `ContinuumBench`.

## Key Files

- `network_profiles.yaml`: public network-regime profiles keyed by name.
- `service_time_profiles.yaml`: public service-time profile sets keyed by workload and stage.
- `src/continuum_bench/calibration/energy_profiles.yaml`: package-owned energy profiles keyed by domain/profile name.
- `src/continuum_bench/calibration/contact_traces/`: package-owned contact-window CSVs used by trace-backed configs.
- `fit_profiles.py`: converts simple measurement records into validated profile YAML.
- `validate_rankings.py`: validates calibration-related payloads and future ranking checks.
- `examples/`: sample measurement inputs used to demonstrate the conversion flow.

## When To Read This

- when you are adding or selecting named calibration assets
- when you need to convert measurements into the YAML format consumed by the benchmark

The current files are intentionally lightweight. They provide:
- explicit placeholder profile schemas
- deterministic YAML inputs for later measured or emulated calibration data
- validation helpers for profile ingestion and future simulator-vs-real ranking checks

These scaffolds support the repo's public calibration workflow:
- `network_profiles.yaml` stores tier-pair network regimes
- `service_time_profiles.yaml` stores workload/stage/node-class service-time profiles
- `energy_profiles.yaml` stores node/link power coefficients used by energy-aware summaries
- `contact_traces/*.csv` stores runtime contact windows with `contact_open`, `contact_bandwidth`, and `contact_latency`
- `fit_profiles.py` validates existing profile YAML or converts simple measurement records into profile YAML
- `validate_rankings.py` validates profile inputs and future controller-ranking payloads

Current status:
- the public repo now includes three calibration profile categories:
  - placeholder profiles for schema/examples
  - emulated calibrated profiles for calibrated sweep variants
  - public-trace-backed / measured-lab profiles plus packaged contact traces for trace-backed sweep variants
- the benchmark can consume selected profiles at build time when configs opt into calibration
- energy profiles are consumed when configs select `calibration.energy_profile`
- contact traces are consumed when configs select `infrastructure.contact_schedule.mode: trace`

Contact traces are runtime transport overlays, not Eclypse structural edge availability.
Temporary closures should be encoded with `contact_open: 0`;
hard link-failure modeling is intentionally not part of the current public
runtime.

Intended workflow:
- collect measurements or emulation records
- convert them with `fit_profiles.py`
- promote selected entries into the package-owned calibration assets
- select them via config calibration keys
- rerun a calibrated or trace-backed paper protocol

Run the conversion commands from the repository root. The current command path is the repo-local script:

```bash
python calibration/fit_profiles.py network --measurements calibration/examples/network_measurements_example.yaml --output calibration/examples/network_profiles_from_measurements.yaml
python calibration/fit_profiles.py service --measurements calibration/examples/service_time_measurements_example.yaml --output calibration/examples/service_time_profiles_from_measurements.yaml
```

Those example measurement files demonstrate the intended top-level `records` schema for future lab measurements or emulation exports.

Concrete promote/select handoff for calibrated reruns:
- `calibration/examples/network_measurements_example.yaml` is intentionally a single-record example so the converted output maps directly to one rerun-selectable profile name.
- The runtime selectors load package-owned calibration assets from `src/continuum_bench/calibration/network_profiles.yaml` and `src/continuum_bench/calibration/service_time_profiles.yaml`.
- Run the conversion commands above, then copy the converted `iot_edge_measured_lab` entry into `src/continuum_bench/calibration/network_profiles.yaml`.
- copy the converted `split_inference_measured_lab` workload into `src/continuum_bench/calibration/service_time_profiles.yaml`.
- Select those promoted names with `calibration.network_profile` and `calibration.service_profile_set` in the calibrated sweep manifest.
- Select the promoted names from a calibrated sweep manifest, for example:

```yaml
overrides:
  calibration:
    network_profile: iot_edge_measured_lab
    service_profile_set: split_inference_measured_lab
```

After promoting the converted entries and updating the chosen manifest, rerun the calibrated or trace-backed sweep.

Expected YAML shapes:

`network_profiles.yaml`

```yaml
schema_version: 1
kind: network_profiles
profiles:
  profile_name:
    source_tier: iot
    target_tier: edge
    regime: stable_low_latency
    latency_ms:
      median: 12.0
      p95: 18.0
    bandwidth_mbps:
      sustained: 85.0
      degraded: 55.0
    loss_percent: 0.1
    source: placeholder
```

`service_time_profiles.yaml`

```yaml
schema_version: 1
kind: service_time_profiles
profiles:
  workload_name:
    stage_name:
      node_class:
        distribution: deterministic
        mean_ms: 18.0
        p95_ms: 24.0
        startup_ms: 120.0
        migration_ms: 250.0
        source: placeholder
```

Simple measurement-record inputs accepted by `fit_profiles.py`:

```yaml
records:
  - name: iot_edge_measured_lab
    source_tier: iot
    target_tier: edge
    regime: stable_low_latency
    median_rtt_ms: 12.0
    p95_rtt_ms: 18.0
    sustained_mbps: 85.0
    degraded_mbps: 55.0
    loss_percent: 0.1
    source: measured_lab_campaign_2026q2
```

```yaml
records:
  - workload: split_inference_measured_lab
    stage: encoder
    node_class: edge
    distribution: deterministic
    mean_ms: 8.2
    p95_ms: 10.8
    startup_ms: 95.0
    migration_ms: 185.0
    source: measured_lab_campaign_2026q2
```

Example measurement files in this directory:
- `examples/network_measurements_example.yaml`
- `examples/service_time_measurements_example.yaml`
