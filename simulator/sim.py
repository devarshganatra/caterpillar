import asyncio
import httpx
import argparse
import random
import yaml
import logging
import json
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List

from contracts.machine_config import MACHINES, MachineConfig
from contracts.events import TelemetryFrame, ContextFrame
from contracts.signing import sign
from backend.app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SimMode(Enum):
    SIM_IDLE = "idle"
    SIM_TRAVEL = "travel"
    SIM_WORKING = "working"

@dataclass
class MachineRunState:
    machine_id: str
    seq: int
    current_rpm: float
    current_speed_kmh: float
    current_hyd_bar: float
    current_temp_c: float
    current_fuel_lph: float
    seatbelt: str
    proximity: dict | None
    cycle_timer: int
    cycle_completed: bool
    operator_id: str
    task_id: str
    mode: SimMode
    
def load_scenario(path: str) -> List[Dict[str, Any]]:
    if not path:
        return []
    with open(path, "r") as f:
        events = yaml.safe_load(f)
        for ev in events:
            # Parse 'at: 30s' to integer seconds
            if "at" in ev and isinstance(ev["at"], str) and ev["at"].endswith("s"):
                ev["at_seconds"] = int(ev["at"][:-1])
            else:
                ev["at_seconds"] = ev.get("at", 0)
        return events

def create_initial_state(machine_id: str, rng: random.Random) -> MachineRunState:
    return MachineRunState(
        machine_id=machine_id,
        seq=1,
        current_rpm=0.0,
        current_speed_kmh=0.0,
        current_hyd_bar=0.0,
        current_temp_c=25.0, # Ambient start
        current_fuel_lph=0.0,
        seatbelt="FASTENED",
        proximity=None,
        cycle_timer=0,
        cycle_completed=False,
        operator_id=f"OP_{rng.randint(1000, 9999)}",
        task_id=f"TSK_{rng.randint(100, 999)}",
        mode=SimMode.SIM_WORKING # Default starting mode
    )

def advance_state(state: MachineRunState, config: MachineConfig, rng: random.Random):
    # Reset one-frame pulse
    if state.cycle_completed:
        state.cycle_completed = False
        state.cycle_timer = 0
    
    # Mode-based physics coherence
    if state.mode == SimMode.SIM_IDLE:
        target_rpm = sum(config.rpm_idle_range) / 2
        target_speed = 0.0
        target_hyd = 30.0
    elif state.mode == SimMode.SIM_TRAVEL:
        target_rpm = config.rpm_working_range[0] + (config.rpm_working_range[1] - config.rpm_working_range[0]) * 0.3
        target_speed = config.speed_max_kmh * 0.6
        target_hyd = 80.0
    else: # SIM_WORKING
        target_rpm = sum(config.rpm_working_range) / 2
        target_speed = 0.5
        target_hyd = 250.0

    # Random walk towards target
    state.current_rpm += (target_rpm - state.current_rpm) * 0.1 + rng.gauss(0, 30)
    state.current_rpm = max(0, min(config.rpm_working_range[1] + 100, state.current_rpm))
    
    state.current_speed_kmh += (target_speed - state.current_speed_kmh) * 0.2 + rng.gauss(0, 0.3)
    state.current_speed_kmh = max(0, min(config.speed_max_kmh, state.current_speed_kmh))
    
    state.current_hyd_bar += (target_hyd - state.current_hyd_bar) * 0.2 + rng.gauss(0, 5.0)
    state.current_hyd_bar = max(0, state.current_hyd_bar)

    # Fuel rate scales with RPM
    rpm_ratio = max(0, (state.current_rpm - config.rpm_idle_range[0]) / (config.rpm_working_range[1] - config.rpm_idle_range[0]))
    target_fuel = config.fuel_rate_idle_lph + (config.fuel_rate_working_lph - config.fuel_rate_idle_lph) * rpm_ratio
    state.current_fuel_lph += (target_fuel - state.current_fuel_lph) * 0.1 + rng.gauss(0, 0.5)
    state.current_fuel_lph = max(0, state.current_fuel_lph)
    
    # Engine temp slow drift
    target_temp = 85.0 if state.mode != SimMode.SIM_IDLE else 60.0
    state.current_temp_c += (target_temp - state.current_temp_c) * 0.01 + rng.gauss(0, 0.1)

    # Cycle completed logic (only while working)
    if state.mode == SimMode.SIM_WORKING:
        state.cycle_timer += 1
        cycle_duration = rng.randint(45, 90)
        if state.cycle_timer >= cycle_duration:
            state.cycle_completed = True

def parse_duration(d) -> int:
    if isinstance(d, int): return d
    if isinstance(d, str) and d.endswith("s"): return int(d[:-1])
    return int(d)

def apply_scenario_overrides(state: MachineRunState, events: List[Dict[str, Any]], elapsed: int):
    for ev in events:
        at = parse_duration(ev.get("at", ev.get("at_seconds", 0)))
        if "repeat" in ev:
            rep = ev["repeat"]
            every = parse_duration(rep.get("every", 1))
            times = rep.get("times", 1)
            # Check if elapsed matches one of the repeat intervals
            for i in range(times):
                if elapsed == at + (i * every):
                    if ev.get("machine_id") == state.machine_id and "set" in rep:
                        for k, v in rep["set"].items():
                            setattr(state, k, v)
        else:
            if elapsed == at and ev.get("machine_id") == state.machine_id and "set" in ev:
                for k, v in ev["set"].items():
                    setattr(state, k, v)

def build_telemetry_frame(state: MachineRunState) -> TelemetryFrame:
    return TelemetryFrame(
        seq=state.seq,
        ts=datetime.now(timezone.utc),
        machine_id=state.machine_id,
        operator_id=state.operator_id,
        task_id=state.task_id,
        engine_rpm=round(state.current_rpm, 1),
        engine_temp_c=round(state.current_temp_c, 1),
        hydraulic_pressure_bar=round(state.current_hyd_bar, 1),
        fuel_rate_lph=round(state.current_fuel_lph, 1),
        speed_kmh=round(state.current_speed_kmh, 1),
        seatbelt=state.seatbelt,
        proximity=state.proximity,
        cycle_completed=state.cycle_completed,
        truck_present=False, # default for now
        hauler_queue_len=0,
        gps=[37.7749, -122.4194],
        sig=""
    )

async def run_machine_loop(machine_id: str, config: MachineConfig, rng: random.Random, 
                           scenario_events: List[Dict], hmac_secret: str, api_url: str):
    state = create_initial_state(machine_id, rng)
    elapsed = 0
    
    async with httpx.AsyncClient() as client:
        while True:
            advance_state(state, config, rng)
            apply_scenario_overrides(state, scenario_events, elapsed)
            
            frame = build_telemetry_frame(state)
            payload = json.loads(frame.model_dump_json())
            payload["sig"] = sign(payload, hmac_secret)
            
            try:
                resp = await client.post(f"{api_url}/ingest/telemetry", json=payload)
                if resp.status_code >= 400:
                    logger.warning(f"{machine_id} -> {resp.status_code}: {resp.text}")
                else:
                    logger.debug(f"{machine_id} -> {resp.status_code}")
            except Exception as e:
                logger.error(f"{machine_id} POST failed: {e}")
                
            state.seq += 1
            elapsed += 1
            
            # Context events from scenario
            for ev in scenario_events:
                if parse_duration(ev.get("at", ev.get("at_seconds", 0))) == elapsed and "context" in ev:
                    ctx_payload = ev["context"]
                    ctx_payload["ts"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    # In real app, site has its own secret. We use a default for the demo.
                    ctx_payload["sig"] = sign(ctx_payload, "default_site_secret")
                    try:
                        await client.post(f"{api_url}/ingest/context", json=ctx_payload)
                    except Exception as e:
                        logger.error(f"Context POST failed: {e}")

            # Sleep scaled by sim_speed
            await asyncio.sleep(1.0 / settings.sim_speed)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", help="Path to scenario YAML")
    parser.add_argument("--machine", help="Run only one machine ID")
    parser.add_argument("--seed", type=int, help="Deterministic seed for RNG", default=None)
    args = parser.parse_args()

    scenario_events = load_scenario(args.scenario)
    
    rng = random.Random(args.seed if args.seed is not None else random.randint(0, 1000000))
    
    api_url = "http://localhost:8000"
    
    tasks = []
    for m_id, config in MACHINES.items():
        if args.machine and m_id != args.machine:
            continue
            
        secret = settings.parsed_machine_hmac_keys.get(m_id, f"secret_{m_id}")
        tasks.append(run_machine_loop(m_id, config, rng, scenario_events, secret, api_url))
        
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
