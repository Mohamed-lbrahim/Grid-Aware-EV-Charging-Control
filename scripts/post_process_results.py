#!/usr/bin/env python3
"""
post_process_results.py - Summarize co-simulation outputs.

Reads:
    - results/cosim/opf_log.csv      (per-interval grid + station metrics)
    - results/cosim/waiting_log.csv  (per-vehicle waiting time)
    - SUMO tripinfo XML (optional)

Produces:
    - results/cosim/summary.json
    - Console table output

Usage:
        python3 scripts/post_process_results.py \
                --opf-log      results/cosim/opf_log.csv \
                --waiting-log  results/cosim/waiting_log.csv \
                --tripinfo-xml "sumo files/charging_results.xml" \
                --stations     scripts/ev_stations.json \
                --out          results/cosim/summary.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


def load_opf_log(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, on_bad_lines='skip')
    print(f"[opf_log] {len(df)} rows, columns: {list(df.columns)}")
    return df


def load_waiting_log(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"[waiting_log] {len(df)} vehicle records")
    return df


def load_tripinfo(path: Path) -> pd.DataFrame:
    """Parse SUMO tripinfo XML into a DataFrame."""
    rows = []
    tree = ET.parse(path)
    for trip in tree.getroot().findall("tripinfo"):
        row = {
            "vehicle_id":    trip.get("id"),
            "depart_s":      float(trip.get("depart", 0)),
            "arrival_s":     float(trip.get("arrival", 0)),
            "duration_s":    float(trip.get("duration", 0)),
            "waiting_s":     float(trip.get("waitingTime", 0)),
            "stop_time_s":   float(trip.get("stopTime", 0)),
        }
        for dev in trip.findall("./devices/device"):
            if dev.get("id") == "device.battery":
                row["soc_final_kwh"]    = float(dev.get("actualBatteryCapacity", 0))
                row["soc_max_kwh"]      = float(dev.get("maximumBatteryCapacity", 1))
                row["soc_final_pct"]    = round(
                    row["soc_final_kwh"] / max(row["soc_max_kwh"], 1) * 100, 2
                )
        rows.append(row)
    df = pd.DataFrame(rows)
    print(f"[tripinfo] {len(df)} vehicle trips parsed")
    return df


def compute_grid_summary(opf: pd.DataFrame) -> dict:
    feasible = opf[opf["opf_feasible"] == True]
    return {
        "total_opf_calls":          len(opf),
        "feasible_opf_calls":       len(feasible),
        "feasibility_rate_pct":     round(len(feasible) / max(len(opf), 1) * 100, 2),
        "v_min_overall_pu":         round(float(feasible["v_min_pu"].min()), 5) if "v_min_pu" in feasible else None,
        "v_min_mean_pu":            round(float(feasible["v_min_pu"].mean()), 5) if "v_min_pu" in feasible else None,
        "trafo_max_overall_pct":    round(float(feasible["trafo_max_pct"].max()), 2) if "trafo_max_pct" in feasible else None,
        "trafo_max_mean_pct":       round(float(feasible["trafo_max_pct"].mean()), 2) if "trafo_max_pct" in feasible else None,
        "trafo_overload_steps":     int(opf.get("trafo_overloads", pd.Series([0])).sum()),
        "v_violation_steps":        int(opf.get("v_violations", pd.Series([0])).sum()),
        "total_ev_energy_kwh":      round(float(feasible["ev_total_alloc_kw"].sum()) * (900 / 3600), 3)
                                    if "ev_total_alloc_kw" in feasible else None,
        "total_curtailment_kwh":    round(float(feasible["ev_curtailment_kw"].sum()) * (900 / 3600), 3)
                                    if "ev_curtailment_kw" in feasible else None,
    }


def compute_waiting_summary(wait: pd.DataFrame) -> dict:
    return {
        "total_vehicles_waited":      len(wait),
        "mean_peak_waiting_time_s":   round(float(wait["peak_waiting_time_s"].mean()), 2),
        "median_peak_waiting_time_s": round(float(wait["peak_waiting_time_s"].median()), 2),
        "max_peak_waiting_time_s":    round(float(wait["peak_waiting_time_s"].max()), 2),
        "vehicles_with_zero_wait":    int((wait["peak_waiting_time_s"] == 0).sum()),
        "mean_wait_by_station":       wait.groupby("station_id")["peak_waiting_time_s"]
                                        .mean().round(2).to_dict(),
    }


def compute_charging_summary(trips: pd.DataFrame) -> dict:
    if trips.empty:
        return {}
    summary = {
        "total_vehicles":              len(trips),
        "mean_stop_time_s":            round(float(trips["stop_time_s"].mean()), 2),
        "median_stop_time_s":          round(float(trips["stop_time_s"].median()), 2),
        "mean_trip_waiting_s":         round(float(trips["waiting_s"].mean()), 2),
    }
    if "soc_final_pct" in trips.columns:
        summary["mean_soc_at_departure_pct"]    = round(float(trips["soc_final_pct"].mean()), 2)
        summary["vehicles_fully_charged_pct"]   = round(
            float((trips["soc_final_pct"] >= 80).sum()) / max(len(trips), 1) * 100, 2
        )
    return summary


def per_station_summary(opf: pd.DataFrame, stations: list[dict]) -> list[dict]:
    rows = []
    for s in stations:
        sid = s["id"]
        alloc_col  = f"{sid}_alloc_kw"
        req_col    = f"{sid}_req_kw"
        wait_col   = f"{sid}_n_waiting"
        charge_col = f"{sid}_n_charging"

        row = {"station_id": sid, "max_kw": s["max_kw"], "slots": s["slots"]}

        feasible = opf[opf["opf_feasible"] == True]
        if alloc_col in feasible.columns:
            row["mean_alloc_kw"]       = round(float(feasible[alloc_col].mean()), 2)
            row["total_energy_kwh"]    = round(float(feasible[alloc_col].sum()) * (900 / 3600), 3)
        if req_col in feasible.columns:
            row["mean_req_kw"]         = round(float(feasible[req_col].mean()), 2)
            curtail = feasible[req_col] - feasible[alloc_col]
            row["mean_curtailment_kw"] = round(float(curtail.clip(lower=0).mean()), 2)
        if wait_col in opf.columns:
            row["mean_vehicles_waiting"]  = round(float(opf[wait_col].mean()), 2)
        if charge_col in opf.columns:
            row["mean_vehicles_charging"] = round(float(opf[charge_col].mean()), 2)

        rows.append(row)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post-process co-simulation outputs into paper-ready summary.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--opf-log", type=Path,
                        default=Path("results/cosim/opf_log.csv"))
    parser.add_argument("--waiting-log", type=Path,
                        default=Path("results/cosim/waiting_log.csv"))
    parser.add_argument("--tripinfo-xml", type=Path, default=None,
                        help="SUMO tripinfo XML (optional, for completion time metrics).")
    parser.add_argument("--stations", type=Path,
                        default=Path("scripts/ev_stations.json"))
    parser.add_argument("--out", type=Path,
                        default=Path("results/cosim/summary.json"))
    return parser.parse_args()


def main() -> int:
    args     = parse_args()
    stations = json.loads(Path(args.stations).read_text())

    opf  = load_opf_log(args.opf_log)
    wait = load_waiting_log(args.waiting_log) if args.waiting_log.exists() else pd.DataFrame()
    trips = load_tripinfo(args.tripinfo_xml) if args.tripinfo_xml and args.tripinfo_xml.exists() else pd.DataFrame()

    summary = {
        "grid":        compute_grid_summary(opf),
        "waiting":     compute_waiting_summary(wait) if not wait.empty else {},
        "charging":    compute_charging_summary(trips),
        "per_station": per_station_summary(opf, stations),
    }

    print("\n" + "═" * 60)
    print("  Co-Simulation Summary")
    print("═" * 60)

    print("\n── Grid ──")
    for k, v in summary["grid"].items():
        print(f"  {k:<35}: {v}")

    if summary["waiting"]:
        print("\n── Waiting Time ──")
        for k, v in summary["waiting"].items():
            if k != "mean_wait_by_station":
                print(f"  {k:<35}: {v}")
        print("  Per-station mean waiting (s):")
        for sid, wt in summary["waiting"].get("mean_wait_by_station", {}).items():
            print(f"    {sid}: {wt} s")

    if summary["charging"]:
        print("\n── Charging Completion ──")
        for k, v in summary["charging"].items():
            print(f"  {k:<35}: {v}")

    print("\n── Per-Station ──")
    ps_df = pd.DataFrame(summary["per_station"])
    print(ps_df.to_string(index=False))
    print("═" * 60 + "\n")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"Summary saved to {args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
