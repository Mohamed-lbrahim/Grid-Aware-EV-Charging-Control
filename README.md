# Grid-Aware-EV-Charging-Control

SUMO–pandapower co-simulation for grid-aware EV charging control.

## Overview

This repo couples SUMO traffic simulation with a pandapower grid model to allocate
charging power at stations while respecting grid constraints. The controller runs
at a fixed interval (default 15 min) and logs both grid and station metrics.

Key points:
- The pandapower network in `pandapower/LV_MV_STATIONS_MERGED.json` was generated
	using the pylovo grid synthesis tool (see references below).
- EV trips/routes were generated with SUMO's `randomTrips.py` utility.

## Repository layout

- `scripts/` - entry points and helpers
	- `cosim/` - co-simulation package (grid ops, SUMO I/O, metrics)
	- `post_process_results.py` - summary statistics from logs
- `pandapower/` - grid model JSON
- `sumo files/` - SUMO network, routes, and additional files
- `visualizations/` - figures and plots

## Requirements

- Python 3.10+ with `pandapower`, `pandas`, `traci`
- SUMO installed and available on PATH (`sumo` or `sumo-gui`)

## Run the co-simulation

From the repo root:

```bash
PYTHONPATH=./scripts python3 -m cosim.co_sim_controller \
	--sumocfg  "sumo files/base.sumocfg" \
	--grid     pandapower/LV_MV_STATIONS_MERGED.json \
	--stations scripts/ev_stations.json \
	--out-dir  results/cosim \
	--control-interval 900 \
	--verbose
```

Outputs:
- `results/cosim/opf_log.csv` - per-interval grid + station metrics
- `results/cosim/waiting_log.csv` - per-vehicle waiting times

## Post-process results

```bash
python3 scripts/post_process_results.py \
	--opf-log      results/cosim/opf_log.csv \
	--waiting-log  results/cosim/waiting_log.csv \
	--tripinfo-xml "sumo files/charging_results.xml" \
	--stations     scripts/ev_stations.json \
	--out          results/cosim/summary.json
```

This produces `results/cosim/summary.json` and prints a console summary.

## Notes

- Paths are relative to the repo root. Run commands from the root to avoid
	missing-file errors.
- To use a different SUMO config or grid file, pass `--sumocfg` and `--grid`.

## References

- pylovo (grid synthesis tool): https://github.com/pylovo/pylovo
- SUMO randomTrips.py (trip generation): https://sumo.dlr.de/docs/Tools/Trip.html