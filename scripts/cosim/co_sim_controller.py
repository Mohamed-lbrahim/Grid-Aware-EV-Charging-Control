#!/usr/bin/env python3
"""
co_sim_controller.py - SUMO/Pandapower co-simulation controller.

Control cycle (default 900 s): read station occupancy, run a PF heuristic,
allocate station power, and log grid/station metrics. Waiting time is tracked
from the lane immediately upstream of each station.

Usage (run from repo root):
    PYTHONPATH=./scripts python3 -m cosim.co_sim_controller \
        --sumocfg  "sumo files/base.sumocfg" \
        --grid     pandapower/LV_MV_STATIONS_MERGED.json \
        --stations scripts/ev_stations.json \
        --out-dir  results/cosim \
        --control-interval 900 \
        --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pandapower as pp

try:
    import traci
except ImportError:
    print("[ERROR] 'traci' not found. Install it via: pip install traci  or  pip install eclipse-sumo")
    sys.exit(1)

from cosim.constants import SAFE_FALLBACK_KW
from cosim.csv_logger import CsvLogger
from cosim.grid_ops import (
    collect_grid_metrics,
    collect_station_metrics,
    run_opf_control_step,
    setup_opf_costs,
)
from cosim.logging_utils import setup_logging
from cosim.sumo_io import apply_allocations_to_sumo, update_waiting_log
from cosim.validation import validate_stations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SUMO/Pandapower co-simulation controller.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sumocfg", type=Path, required=True,
                        help="Path to SUMO .sumocfg file.")
    parser.add_argument("--grid", type=Path,
                        default=Path("pandapower/LV_MV_STATIONS_MERGED.json"),
                        help="Pandapower grid JSON (with EV buses injected).")
    parser.add_argument("--stations", type=Path,
                        default=Path("scripts/ev_stations.json"),
                        help="EV stations JSON with SUMO lane IDs and pp indices.")
    parser.add_argument("--out-dir", type=Path,
                        default=Path("results/cosim"),
                        help="Output directory for CSV logs.")
    parser.add_argument("--control-interval", type=int, default=900,
                        help="OPF control interval in SUMO seconds (default: 900 = 15 min).")
    parser.add_argument("--fallback-kw", type=float, default=SAFE_FALLBACK_KW,
                        help="Safe per-station kW used when heuristic fails.")
    parser.add_argument("--sumo-binary", type=str, default="sumo",
                        help="SUMO binary (use 'sumo-gui' for visual mode).")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = setup_logging(args.verbose)

    logger.info("Loading grid from %s", args.grid)
    net = pp.from_json(str(args.grid))
    logger.info("Grid: %d buses, %d loads, %d trafos", len(net.bus), len(net.load), len(net.trafo))

    stations: list[dict] = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    logger.info("Loaded %d stations from %s", len(stations), args.stations)

    if not validate_stations(stations, net, logger):
        logger.error("Station config validation failed - update ev_stations.json before running.")
        return 1

    setup_opf_costs(net, stations, logger)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    opf_log_path = args.out_dir / "opf_log.csv"
    waiting_csv = args.out_dir / "waiting_log.csv"

    if opf_log_path.exists():
        opf_log_path.unlink()
    if waiting_csv.exists():
        waiting_csv.unlink()

    opf_log = CsvLogger(opf_log_path)

    sumo_cmd = [args.sumo_binary, "-c", str(args.sumocfg), "--no-step-log", "true"]
    logger.info("Starting SUMO: %s", " ".join(sumo_cmd))
    traci.start(sumo_cmd)

    step = 0
    ctrl_int = args.control_interval
    waiting_log: dict[str, dict] = {}
    allocations: dict[str, float] = {s["id"]: 0.0 for s in stations}

    logger.info("Starting co-simulation loop (OPF every %d s)...", ctrl_int)

    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        step += 1

        update_waiting_log(stations, waiting_log, step)

        if step % ctrl_int == 0:
            t_idx = (step // ctrl_int) % 96
            logger.info("OPF step at sim-second=%d (t_idx=%d)", step, t_idx)

            feasible, allocations = run_opf_control_step(
                net,
                stations,
                args.fallback_kw,
                logger,
            )
            apply_allocations_to_sumo(stations, allocations, logger)

            grid_m = collect_grid_metrics(net, feasible)
            station_m = collect_station_metrics(stations, allocations)

            row = {"step": step, "sim_time_s": step, "t_idx": t_idx, **grid_m, **station_m}
            opf_log.append(row)
            opf_log.flush()

            logger.info(
                "  V_min=%.4f pu | trafo_max=%.1f%% | EV alloc=%.1f/%.1f kW | feasible=%s",
                grid_m.get("v_min_pu", float("nan")),
                grid_m.get("trafo_max_pct", float("nan")),
                station_m.get("ev_total_alloc_kw", 0.0),
                station_m.get("ev_total_req_kw", 0.0),
                feasible,
            )

    traci.close()
    logger.info("SUMO simulation finished at step=%d", step)

    wait_rows = []
    for veh_id, rec in waiting_log.items():
        wait_rows.append({
            "vehicle_id": veh_id,
            "station_id": rec["station_id"],
            "entry_step": rec["entry_step"],
            "last_seen_step": rec["last_step"],
            "peak_waiting_time_s": round(rec["peak_wait_s"], 2),
            "total_wait_steps": rec["total_wait_steps"],
            "finalized": rec["finalized"],
        })

    pd.DataFrame(
        wait_rows,
        columns=[
            "vehicle_id",
            "station_id",
            "entry_step",
            "last_seen_step",
            "peak_waiting_time_s",
            "total_wait_steps",
            "finalized",
        ],
    ).to_csv(waiting_csv, index=False)

    if wait_rows:
        logger.info("Waiting log saved: %d vehicle records -> %s", len(wait_rows), waiting_csv)
    else:
        logger.info("No vehicles observed on waiting lanes. Saved empty log to %s", waiting_csv)

    logger.info("Done. Outputs in %s", args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
