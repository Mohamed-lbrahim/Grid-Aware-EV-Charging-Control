import logging

import pandapower as pp


def validate_stations(stations: list[dict], net: pp.pandapowerNet, logger: logging.Logger) -> bool:
    """Check that all pp_load_idx and pp_bus_idx exist in the grid."""
    ok = True
    for s in stations:
        if s["pp_load_idx"] not in net.load.index:
            logger.error("Station '%s': pp_load_idx=%d not in net.load", s["id"], s["pp_load_idx"])
            ok = False
        if s["pp_bus_idx"] not in net.bus.index:
            logger.error("Station '%s': pp_bus_idx=%d not in net.bus", s["id"], s["pp_bus_idx"])
            ok = False
        for field in ("sumo_cs_id", "sumo_charging_lane_id", "sumo_waiting_lane_id"):
            if s.get(field, "FILL_FROM_ADD_XML") == "FILL_FROM_ADD_XML":
                logger.error("Station '%s': field '%s' is a placeholder — update ev_stations.json", s["id"], field)
                ok = False
    return ok
