# Package Calibration Assets

## Purpose

This directory stores the package-owned calibration assets that benchmark configs can opt into at runtime.

## Key Files

- `network_profiles.yaml`: named network profiles for supported tier pairs.
- `service_time_profiles.yaml`: named service-time profile sets for workloads, stages, and node classes.
- `energy_profiles.yaml`: named node/link power profiles used by energy-aware configs.
- `contact_traces/`: packaged CSV contact-window schedules used by public contact-aware configs.

## When To Read This

- when you are adding or auditing the named calibration assets shipped with the package
- when a config selects `calibration.network_profile`, `calibration.service_profile_set`, or `calibration.energy_profile` and you need to inspect the source data
- when a config selects `infrastructure.contact_schedule.path`

Loader and validation code lives one level up in `continuum_bench.calibration_profiles`.
Contact-trace parsing lives in `continuum_bench.infra.contact_schedule`.

Contact trace CSVs use `start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open`.
The `contact_open` field is a runtime transport signal for ContinuumBench
buffering and delivery. It is intentionally separate from Eclypse structural
edge availability, so temporary contact closures do not make structural
placement infeasible.
