from __future__ import annotations

import logging


def get_vehicles_on_lane(lane_id: str) -> list[str]:
    """Return list of vehicle IDs currently on the given lane."""
    import traci

    try:
        return list(traci.lane.getLastStepVehicleIDs(lane_id))
    except traci.TraCIException:
        return []


def set_station_charging_power_w(cs_id: str, power_w: float) -> None:
    """Set maximum charging power (Watts per vehicle) for a charging station."""
    import traci

    try:
        traci.chargingstation.setChargingPower(cs_id, max(0.0, power_w))
    except traci.TraCIException:
        pass


def get_vehicle_waiting_time_s(veh_id: str) -> float:
    """Return cumulative waiting time (s) for a vehicle."""
    import traci

    try:
        return float(traci.vehicle.getWaitingTime(veh_id))
    except traci.TraCIException:
        return 0.0


def update_waiting_log(
    stations: list[dict],
    waiting_log: dict[str, dict],
    step: int,
) -> None:
    """Track vehicles on waiting lanes and accumulate wait stats."""
    import traci

    for s in stations:
        waiting_vehs = set(get_vehicles_on_lane(s["sumo_waiting_lane_id"]))

        all_on_charge_lane = set(get_vehicles_on_lane(s["sumo_charging_lane_id"]))
        try:
            active_charging = set(traci.chargingstation.getVehicleIDs(s["sumo_cs_id"]))
        except traci.TraCIException:
            active_charging = set()

        waiting_vehs.update(all_on_charge_lane - active_charging)

        for veh_id in waiting_vehs:
            wt = get_vehicle_waiting_time_s(veh_id)
            if veh_id not in waiting_log:
                waiting_log[veh_id] = {
                    "station_id": s["id"],
                    "entry_step": step,
                    "last_step": step,
                    "peak_wait_s": wt,
                    "total_wait_steps": 1,
                    "finalized": False,
                }
            else:
                rec = waiting_log[veh_id]
                rec["last_step"] = step
                rec["peak_wait_s"] = max(rec["peak_wait_s"], wt)
                rec["total_wait_steps"] += 1

        for veh_id, rec in waiting_log.items():
            if not rec["finalized"] and rec["station_id"] == s["id"]:
                if veh_id not in waiting_vehs and rec["last_step"] < step:
                    rec["finalized"] = True


def apply_allocations_to_sumo(
    stations: list[dict],
    allocations: dict[str, float],
    logger: logging.Logger,
) -> None:
    """Write station-level charging power (Watts) for each station."""
    for s in stations:
        p_total_kw = allocations.get(s["id"], 0.0)
        min_kw = s.get("min_kw", 15.0)
        advertised_kw = max(min_kw, p_total_kw)
        w_total = advertised_kw * 1000.0

        set_station_charging_power_w(s["sumo_cs_id"], w_total)

        logger.debug(
            "Station %s: %.2f kW total allocation (Advertised: %.2f kW)",
            s["id"],
            p_total_kw,
            advertised_kw,
        )
