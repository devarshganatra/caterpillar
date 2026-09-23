"""
Single source of truth for the ML feature/label vocabulary — imported by
both training (ml/generate_history.py, ml/train_*.py) and serving
(backend/app/services/eta.py, attribution.py, anomaly.py in later batches).
Values documented as "assumption" are not specified in BUILD_PLAN_new.md /
ARCHITECTURE_new.md and are recorded here + in PROGRESS.md so they can be
revisited.
"""

# --- Task types -------------------------------------------------------
# cycle_s = the simulator's per-cycle duration used for base_min = target_cycles * cycle_s / 60
TASK_TYPES = {
    "TRENCHING":        {"machine_types": ["EXCAVATOR"],            "cycle_s": 55},
    "TRUCK_LOADING":     {"machine_types": ["EXCAVATOR", "LOADER"], "cycle_s": 45},
    "BULK_EXCAVATION":   {"machine_types": ["EXCAVATOR"],            "cycle_s": 60},
    "GRADING":           {"machine_types": ["LOADER"],               "cycle_s": 70},
    "STOCKPILE_MGMT":    {"machine_types": ["LOADER"],               "cycle_s": 50},
}
TASK_TYPE_NAMES = sorted(TASK_TYPES.keys())

MACHINE_TYPES = ["EXCAVATOR", "LOADER"]
SKILL_LEVELS = ["EXPERT", "INTERMEDIATE", "BEGINNER"]
WEATHER_LEVELS = ["SUNNY", "CLOUDY", "WINDY", "RAIN"]
GROUND_LEVELS = ["DRY", "WET", "MUDDY", "ICY"]

# --- Duration multipliers (BUILD_PLAN_new.md Stage 1 M2 generator spec) --
WEATHER_M = {"SUNNY": 1.0, "CLOUDY": 1.03, "WINDY": 1.10, "RAIN": 1.18}
SKILL_M = {"EXPERT": 0.93, "INTERMEDIATE": 1.05, "BEGINNER": 1.30}
PLANNER_SKILL_M = {"EXPERT": 0.95, "INTERMEDIATE": 1.0, "BEGINNER": 1.10}  # the "naive" planner estimate

# ASSUMPTION (not specified in BUILD_PLAN/ARCH — ARCH only lists ground as a
# safety-envelope condition, not a duration multiplier). Recorded in PROGRESS.md.
GROUND_M = {"DRY": 1.0, "WET": 1.06, "MUDDY": 1.15, "ICY": 1.25}

# --- Window (30s demo-compressed) aggregate features ---------------------
WINDOW_FEATURES = [
    "fuel_per_cycle", "rpm_mean", "rpm_std", "hyd_p95",
    "idle_ratio", "cycle_time_mean", "cycle_time_cv", "temp_slope",
]

# --- ETA model features (ml/train_eta.py, backend/app/services/eta.py) ---
ETA_NUMERIC = [
    "target_cycles", "machine_age_years", "rainfall_mm_h", "visibility_m",
    "wind_kmh", "ambient_temp_c", "hauler_queue_mean",
]
ETA_CATEGORICAL = {
    "task_type": TASK_TYPE_NAMES,
    "machine_type": MACHINE_TYPES,
    "operator_skill": SKILL_LEVELS,
    "weather": WEATHER_LEVELS,
    "ground": GROUND_LEVELS,
}
ETA_FACTOR_GROUPS = {
    "Weather": ["weather", "rainfall_mm_h", "visibility_m", "wind_kmh", "ambient_temp_c"],
    "Ground": ["ground"],
    "Operator": ["operator_skill"],
    "Machine": ["machine_type", "machine_age_years"],
    "Site queue": ["hauler_queue_mean"],
    "Task scope": ["task_type", "target_cycles"],
}

# --- Idle attribution causes (ARCH section 6.6, priority order) ----------
IDLE_CAUSES = ["PLANNED", "MACHINE", "WEATHER", "SITE", "OPERATOR"]

# --- Anomaly types injected by the generator (~3% of windows) ------------
ANOMALY_TYPES = ["HIGH_FUEL_PER_CYCLE", "ERRATIC_CYCLES", "HOT_ENGINE"]
ANOMALY_RATE = 0.03

# --- Operators (synthetic, generated once per history run) ---------------
N_OPERATORS = 8
OPERATOR_SKILL_MIX = (
    ["EXPERT"] * 2 + ["INTERMEDIATE"] * 4 + ["BEGINNER"] * 2
)  # exactly 8, matches BUILD_PLAN "8 operators (skill mix)"
HIGH_IDLE_OPERATOR_FRACTION = 1 / 8  # BUILD_PLAN: "heavy-tailed for ~15% of operators" (approx w/ 8 ops -> 1)

# --- Sites -----------------------------------------------------------
SITES = {
    "SITE-A": {"hauler_count": 3},
    "SITE-B": {"hauler_count": 2},
}

GENERATOR_VERSION = "1.0.0"
