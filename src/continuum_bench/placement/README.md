# Placement Strategies

This directory contains the placement strategies used by the baseline controllers.
Eclypse native strategies are the first choice; ContinuumBench strategies are
extensions for benchmark-specific anchors, resource dimensions, and policy
comparisons that Eclypse does not provide directly.

All runtime placement decisions are audited through Eclypse `Placement` and
`PlacementManager` objects. Strategy code may score benchmark policies, but
candidate feasibility must respect Eclypse `PlacementView` residual resources
and Eclypse path resources. Missing Eclypse paths are infeasible; they are not
repaired by ContinuumBench.

Current strategy classes include:
- `MinCTStrategy`
- `NearestFeasibleEdgeStrategy`
- `CostGreedyStrategy`
- `DependencyAwareStrategy` / `CriticalPathStrategy`
- `RoundRobinStrategy`, an Eclypse round-robin subclass with benchmark anchor/resource guards
- `EclypsePlacementAdapter`, the runtime bridge that materializes controller mappings as Eclypse placements

How to interpret them:
- `best_fit`: Eclypse-native best-fit baseline, preferred for substrate-native checks
- `round_robin`: Eclypse-native round-robin when possible, with ContinuumBench extensions for anchor/resource cases
- `min_latency`: greedy latency-oriented placement
- `nearest_feasible_edge`: edge-preference baseline under anchor constraints
- `cost_greedy`: simple compute+transfer-cost baseline
- `dependency_aware`: role-sensitive critical-path/frontier baseline

Benchmark role:
- these strategies provide stronger heuristic references before introducing learning-based methods or oracle comparisons
- they should be meaningfully distinct, not just renamed aliases of the same policy

If you add a new strategy:
- keep it deterministic
- keep the assumptions explicit
- add tests that prove it differs from nearby baselines on a representative benchmark shape
