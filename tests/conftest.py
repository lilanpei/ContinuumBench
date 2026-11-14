import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parents[1] / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ECLYPSE is an external substrate dependency (see README); the runtime/placement stack imports it
# at module load time. When it is not installed, skip the test modules that need it at collection
# time so a bare ``pytest`` reports passed/skipped instead of aborting with collection errors.
try:
    _HAS_ECLYPSE = importlib.util.find_spec("eclypse") is not None
except Exception:
    _HAS_ECLYPSE = False

if not _HAS_ECLYPSE:
    collect_ignore = [
        "controllers/test_constant.py",
        "test_ai_readiness.py",
        "test_anticipatory_controller.py",
        "test_benchmark_reproducibility.py",
        "test_calibration_profiles.py",
        "test_cli.py",
        "test_cli_smoke.py",
        "test_contact_schedule.py",
        "test_deadline_by_priority.py",
        "test_intra_tier_links.py",
        "test_placement_strategies.py",
        "test_realism_tiers.py",
        "test_runner_integration.py",
        "test_scenarios.py",
        "test_skeleton_service.py",
        "test_slo_discarded_work.py",
        "test_sweeps.py",
        "test_worker_pool_scaling.py",
    ]
