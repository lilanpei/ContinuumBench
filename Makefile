# ContinuumBench reproduction targets. See REPRODUCE.md for the full guide.
#
# Prerequisites: the package installed (pip install -e ".[milp,figures,dev]") and the external ECLYPSE
# substrate (eclypse 0.8.1, commit 73806b7) available via ECLYPSE_HOME or pip. Override the
# interpreter with PY=... (e.g. PY=python3.11) and the output root with RUNS=...
PY   ?= python3
RUNS ?= runs
FIGS ?= figs

# The sweeps of the ContinuumBench paper; REPRODUCE.md maps each one to its table or finding.
PAPER_SWEEPS := main sensitivity_load sensitivity_contact sensitivity_failure \
                replan_ablation replan_milp load_sweep placement_sweep
# Sweeps aggregated into analysis/ tables (the main comparison and the stressor sweeps).
TABLE_SWEEPS := main sensitivity_load sensitivity_contact sensitivity_failure
# Further sweeps, not reported in the paper; `make figures` plots them with the paper's sweeps.
EXTRA_SWEEPS := realism_ladder delay_breakdown milp_reference

.PHONY: help test smoke sweeps analysis findings reproduce extra-sweeps figures

help:
	@echo "ContinuumBench make targets:"
	@echo "  make test          - run the regression suite (skips ECLYPSE-only tests if eclypse is absent)"
	@echo "  make smoke         - fast end-to-end check (sota_pilot, 3 seeds, 80+40 epochs; minutes)"
	@echo "  make sweeps        - run the paper's sweeps into \$$(RUNS)/ (about 2,200 simulations)"
	@echo "  make analysis      - aggregate the main and stressor sweeps; score robustness of the main one"
	@echo "  make findings      - print the numbers of Table 3, Findings 1 to 4 and the stressor sweeps"
	@echo "  make reproduce     - sweeps + analysis + findings (the full paper)"
	@echo "  make extra-sweeps  - run and aggregate the further sweeps that the paper does not report"
	@echo "  make figures       - plot the analysis outputs into \$$(FIGS)/ (needs reproduce and extra-sweeps)"

test:
	$(PY) -m pytest tests -q

# Fast sanity path: exercises sweep -> aggregate -> tables end to end without the full matrix.
smoke:
	$(PY) -m continuum_bench.cli sweep --manifest sweeps/sota_pilot.yaml --out $(RUNS)/sota_pilot
	$(PY) analysis/aggregate_runs.py --sweep-dir $(RUNS)/sota_pilot --out $(RUNS)/sota_pilot/analysis
	$(PY) analysis/tables.py --aggregate-csv $(RUNS)/sota_pilot/analysis/aggregate.csv --out $(RUNS)/sota_pilot/analysis
	@echo "smoke OK: sweep -> aggregate -> tables path works"

sweeps:
	@for s in $(PAPER_SWEEPS); do \
	  echo ">> sweep $$s"; \
	  $(PY) -m continuum_bench.cli sweep --manifest sweeps/$$s.yaml --out $(RUNS)/$$s || exit 1; \
	done

analysis:
	@for s in $(TABLE_SWEEPS); do \
	  echo ">> analysis $$s"; \
	  $(PY) analysis/aggregate_runs.py --sweep-dir $(RUNS)/$$s --out $(RUNS)/$$s/analysis || exit 1; \
	  $(PY) analysis/tables.py --aggregate-csv $(RUNS)/$$s/analysis/aggregate.csv --out $(RUNS)/$$s/analysis || exit 1; \
	done
	$(PY) analysis/score_sensitivity.py --grouped-csv $(RUNS)/main/analysis/grouped_strict.csv

findings:
	$(PY) analysis/findings.py main --sweep-dir $(RUNS)/main
	$(PY) analysis/findings.py replan --sweep-dir $(RUNS)/replan_ablation
	$(PY) analysis/findings.py replan-milp --sweep-dir $(RUNS)/replan_milp
	$(PY) analysis/findings.py load --sweep-dir $(RUNS)/load_sweep
	$(PY) analysis/findings.py placement --sweep-dir $(RUNS)/placement_sweep
	$(PY) analysis/findings.py failure --sweep-dir $(RUNS)/sensitivity_failure
	$(PY) analysis/findings.py stressors --contact-dir $(RUNS)/sensitivity_contact --load-dir $(RUNS)/sensitivity_load

reproduce: sweeps analysis findings
	@echo "Reproduction complete: REPRODUCE.md lists the values to expect."

extra-sweeps:
	@for s in $(EXTRA_SWEEPS); do \
	  echo ">> sweep $$s"; \
	  $(PY) -m continuum_bench.cli sweep --manifest sweeps/$$s.yaml --out $(RUNS)/$$s || exit 1; \
	  $(PY) analysis/aggregate_runs.py --sweep-dir $(RUNS)/$$s --out $(RUNS)/$$s/analysis || exit 1; \
	  $(PY) analysis/tables.py --aggregate-csv $(RUNS)/$$s/analysis/aggregate.csv --out $(RUNS)/$$s/analysis || exit 1; \
	done

figures:
	$(PY) analysis/figures.py \
	  --main $(RUNS)/main/analysis/grouped_strict.csv \
	  --main-aggregate $(RUNS)/main/analysis/aggregate.csv \
	  --load $(RUNS)/sensitivity_load/analysis/grouped_strict.csv \
	  --contact $(RUNS)/sensitivity_contact/analysis/grouped_strict.csv \
	  --delay $(RUNS)/delay_breakdown/analysis/grouped_strict.csv \
	  --rank-stability $(RUNS)/realism_ladder/analysis/rank_stability.csv \
	  --rankings $(RUNS)/realism_ladder/analysis/rankings.csv \
	  --regime-grouped $(RUNS)/realism_ladder/analysis/grouped_strict.csv \
	  --out $(FIGS)
