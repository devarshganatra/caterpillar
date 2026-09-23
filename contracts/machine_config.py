from dataclasses import dataclass
from typing import Literal, Dict, Tuple

@dataclass
class MachineConfig:
    machine_id: str
    site_id: str
    type: Literal["EXCAVATOR", "LOADER"]
    model: str
    age_years: int
    # Simulation profile derived from type + age
    rpm_idle_range: Tuple[int, int]
    rpm_working_range: Tuple[int, int]
    hyd_working_range: Tuple[int, int]
    speed_max_kmh: float
    fuel_rate_idle_lph: float
    fuel_rate_working_lph: float

MACHINES: Dict[str, MachineConfig] = {
    "EXC001": MachineConfig(
        machine_id="EXC001", site_id="SITE-A", type="EXCAVATOR", model="320", age_years=2,
        rpm_idle_range=(800, 1000), rpm_working_range=(1800, 2200),
        hyd_working_range=(200, 320), speed_max_kmh=5.5,
        fuel_rate_idle_lph=3.5, fuel_rate_working_lph=18.0
    ),
    "EXC002": MachineConfig(
        machine_id="EXC002", site_id="SITE-A", type="EXCAVATOR", model="320", age_years=4,
        rpm_idle_range=(800, 1000), rpm_working_range=(1800, 2200),
        hyd_working_range=(200, 320), speed_max_kmh=5.5,
        fuel_rate_idle_lph=3.8, fuel_rate_working_lph=19.5
    ),
    "EXC003": MachineConfig(
        machine_id="EXC003", site_id="SITE-B", type="EXCAVATOR", model="336", age_years=1,
        rpm_idle_range=(800, 1000), rpm_working_range=(1700, 2000),
        hyd_working_range=(250, 350), speed_max_kmh=4.5,
        fuel_rate_idle_lph=5.0, fuel_rate_working_lph=28.0
    ),
    "EXC004": MachineConfig(
        machine_id="EXC004", site_id="SITE-B", type="EXCAVATOR", model="336", age_years=5,
        rpm_idle_range=(800, 1000), rpm_working_range=(1700, 2000),
        hyd_working_range=(250, 350), speed_max_kmh=4.5,
        fuel_rate_idle_lph=5.5, fuel_rate_working_lph=30.0
    ),
    "LDR001": MachineConfig(
        machine_id="LDR001", site_id="SITE-A", type="LOADER", model="950", age_years=3,
        rpm_idle_range=(700, 900), rpm_working_range=(1600, 2100),
        hyd_working_range=(180, 280), speed_max_kmh=40.0,
        fuel_rate_idle_lph=4.0, fuel_rate_working_lph=15.0
    ),
    "LDR002": MachineConfig(
        machine_id="LDR002", site_id="SITE-B", type="LOADER", model="980", age_years=2,
        rpm_idle_range=(700, 900), rpm_working_range=(1500, 2000),
        hyd_working_range=(220, 300), speed_max_kmh=40.0,
        fuel_rate_idle_lph=6.0, fuel_rate_working_lph=25.0
    ),
}
