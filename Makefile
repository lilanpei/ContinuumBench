# ContinuumBench reproduction targets. See REPRODUCE.md for the full guide.
#
# Prerequisites: the package installed (pip install -e ".[milp,figures,dev]") and the external ECLYPSE
# substrate (eclypse 0.8.1, commit 73806b7) available via ECLYPSE_HOME or pip. Override the
# interpreter with PY=... (e.g. PY=python3.11) and the output root with RUNS=...
PY   ?= python3
RUNS ?= runs
FIGS ?= figs

# Paper sweeps, in dependency order for the figures/tables.
PAPER_SWEEPS := main realism_ladder sensitivity_load \
                sensitivity_contact sensitivity_failure \
                delay_breakdown milp_reference

.PHONY: help test smoke sweeps analysis figures reproduce

help:
	@echo "ContinuumBench make targets:"
	@echo "  make test       - run the regression suite (skips ECLYPSE-only tests if eclypse absent)"
	@echo "  make smoke      - fast end-to-end check (sota_pilot, 3 seeds, 80+40 epochs; minutes)"
	@echo "  make sweeps     - run all paper sweeps into \$$(RUNS)/ (long; ~1800 simulations)"
	@echo "  make analysis   - aggregate + build tables/score for each paper sweep"
	@echo "  make figures    - render all paper figures into \$$(FIGS) from the analysis outputs"
	@echo "  make reproduce  - sweeps + analysis + figures (full paper reproduction)"

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
	@for s in $(PAPER_SWEEPS); do \
	  echo ">> analysis $$s"; \
	  $(PY) analysis/aggregate_runs.py --sweep-dir $(RUNS)/$$s --out $(RUNS)/$$s/analysis || exit 1; \
	  $(PY) analysis/tables.py --aggregate-csv $(RUNS)/$$s/analysis/aggregate.csv --out $(RUNS)/$$s/analysis || exit 1; \
	done
	$(PY) analysis/score_sensitivity.py \
	  --grouped-csv $(RUNS)/main/analysis/grouped_strict.csv \
	  --out $(RUNS)/main/analysis/score_sensitivity.json
	$(PY) analysis/significance.py \
	  --aggregate-csv $(RUNS)/main/analysis/aggregate.csv \
	  --out $(RUNS)/main/analysis/significance.json

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

reproduce: sweeps analysis figures
	@echo "Reproduction complete. Table 1 -> $(RUNS)/main/analysis/grouped_strict.csv ; figures -> $(FIGS)/"
