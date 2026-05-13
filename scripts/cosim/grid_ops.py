from __future__ import annotations

import logging
from typing import Any

import pandapower as pp

from cosim.constants import LOADING_MAX, VOLTAGE_HI, VOLTAGE_LO
from cosim.sumo_io import get_vehicles_on_lane


def setup_opf_costs(net: pp.pandapowerNet, stations: list[dict], logger: logging.Logger) -> None:
    """Set costs so OPF maximizes total EV power delivered."""
    load_indices = {s["pp_load_idx"] for s in stations}
    for idx in load_indices:
        if idx not in net.load.index:
            logger.warning("pp_load_idx=%d not found in net.load — skipping cost.", idx)
            continue
        pp.create_poly_cost(net, element=idx, et="load", cp1_eur_per_mw=-1.0)
        logger.debug("OPF cost set for load idx=%d (EV station).", idx)

    logger.info("OPF cost function configured: maximize sum p_ev across %d stations.", len(stations))


def run_opf_control_step(
    net: pp.pandapowerNet,
    stations: list[dict],
    fallback_kw: float,
    logger: logging.Logger,
) -> tuple[bool, dict[str, float]]:
    """Update demand bounds, run a PF heuristic, return (feasible, allocations)."""
    import traci

    allocations: dict[str, float] = {}
    station_state: dict[str, dict[str, float]] = {}

    for s in stations:
        try:
            active_charging = traci.chargingstation.getVehicleIDs(s["sumo_cs_id"])
            n_charging = len(active_charging)
        except traci.TraCIException:
            n_charging = 0

        slots = max(s["slots"], 1)
        occ_ratio = min(1.0, n_charging / slots)
        p_max_mw = (occ_ratio * s["max_kw"]) / 1000.0

        station_state[s["id"]] = {
            "n_charging": float(n_charging),
            "p_req_kw": occ_ratio * s["max_kw"],
        }

        if s["pp_load_idx"] in net.load.index:
            net.load.at[s["pp_load_idx"], "p_mw"] = p_max_mw

    feasible = False
    for attempt in range(30):
        try:
            pp.runpp(net)
        except pp.LoadflowNotConverged:
            logger.warning("Heuristic PF: runpp failed to converge at attempt %d", attempt)
            break

        tl = net.res_trafo["loading_percent"]
        vl = net.res_bus["vm_pu"]

        trafo_overloads = int((tl > LOADING_MAX).sum()) if not tl.empty else 0
        v_violations = int((vl < VOLTAGE_LO).sum()) if not vl.empty else 0

        if trafo_overloads == 0 and v_violations == 0:
            feasible = True
            break

        for s in stations:
            if s["pp_load_idx"] in net.load.index:
                net.load.at[s["pp_load_idx"], "p_mw"] *= 0.90

    if feasible:
        for s in stations:
            if s["pp_load_idx"] in net.load.index:
                p_opt_kw = float(net.load.at[s["pp_load_idx"], "p_mw"]) * 1000.0
                allocations[s["id"]] = round(p_opt_kw, 4)
            else:
                allocations[s["id"]] = 0.0
    else:
        logger.warning(
            "Heuristic PF did not converge — applying safe fallback of %.2f kW per station.",
            fallback_kw,
        )
        for s in stations:
            state = station_state.get(s["id"], {"p_req_kw": 0.0, "n_charging": 0.0})
            req_kw = float(state.get("p_req_kw", 0.0))
            station_min_kw = float(s.get("min_kw", fallback_kw))
            fallback_station_kw = min(station_min_kw, s["max_kw"], req_kw)
            if state.get("n_charging", 0.0) <= 0:
                fallback_station_kw = 0.0

            if s["pp_load_idx"] in net.load.index:
                net.load.at[s["pp_load_idx"], "p_mw"] = fallback_station_kw / 1000.0

            allocations[s["id"]] = round(fallback_station_kw, 4)

    return feasible, allocations


def collect_grid_metrics(net: pp.pandapowerNet, feasible: bool) -> dict[str, Any]:
    """Extract post-PF grid health metrics from res_bus/line/trafo."""
    m: dict[str, Any] = {"opf_feasible": feasible}

    if feasible:
        if not net.res_bus.empty:
            vm = net.res_bus["vm_pu"]
            m["v_min_pu"] = round(float(vm.min()), 5)
            m["v_max_pu"] = round(float(vm.max()), 5)
            m["v_violations"] = int((vm < VOLTAGE_LO).sum() + (vm > VOLTAGE_HI).sum())

        if not net.res_line.empty:
            ll = net.res_line["loading_percent"]
            m["line_max_pct"] = round(float(ll.max()), 2)
            m["line_overloads"] = int((ll > LOADING_MAX).sum())

        if not net.res_trafo.empty:
            tl = net.res_trafo["loading_percent"]
            m["trafo_max_pct"] = round(float(tl.max()), 2)
            m["trafo_overloads"] = int((tl > LOADING_MAX).sum())

    return m


def collect_station_metrics(
    stations: list[dict],
    allocations: dict[str, float],
) -> dict[str, Any]:
    """Per-station occupancy and allocation metrics."""
    import traci

    m: dict[str, Any] = {}
    total_req = 0.0
    total_alloc = 0.0

    for s in stations:
        try:
            active_charging = set(traci.chargingstation.getVehicleIDs(s["sumo_cs_id"]))
        except traci.TraCIException:
            active_charging = set()

        n_charging = len(active_charging)

        all_on_charge_lane = set(get_vehicles_on_lane(s["sumo_charging_lane_id"]))
        n_waiting_lane = len(get_vehicles_on_lane(s["sumo_waiting_lane_id"]))
        n_waiting = n_waiting_lane + len(all_on_charge_lane - active_charging)

        requested = (n_charging / max(s["slots"], 1)) * s["max_kw"]
        allocated = allocations.get(s["id"], 0.0)

        m[f"{s['id']}_n_charging"] = n_charging
        m[f"{s['id']}_n_waiting"] = n_waiting
        m[f"{s['id']}_req_kw"] = round(requested, 2)
        m[f"{s['id']}_alloc_kw"] = round(allocated, 2)
        m[f"{s['id']}_curtail_kw"] = round(max(requested - allocated, 0.0), 2)

        total_req += requested
        total_alloc += allocated

    m["ev_total_req_kw"] = round(total_req, 2)
    m["ev_total_alloc_kw"] = round(total_alloc, 2)
    m["ev_curtailment_kw"] = round(max(total_req - total_alloc, 0.0), 2)
    return m
