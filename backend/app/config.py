from pydantic_settings import BaseSettings
from pydantic import field_validator

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://cat:cat@localhost:5432/catcopilot"
    redis_url: str = "redis://localhost:6379/0"
    machine_hmac_keys: str = ""  # raw string from .env, e.g. "EXC001:s1,LDR001:s2"
    jwt_secret: str = "placeholder_jwt_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120
    sim_speed: int = 10
    log_level: str = "INFO"

    # ML / intelligence layer (Stage 3)
    ml_artifacts_dir: str = "ml/artifacts"
    eta_slip_threshold: float = 0.10          # ARCH: "ETA slip over 10% emits ETA_SLIP"
    eta_slip_warning_threshold: float = 0.25
    eta_min_cycles_for_blend: int = 3

    # Idle attribution (ARCH 6.6)
    planned_breaks_utc: str = ""              # "HH:MM-HH:MM,HH:MM-HH:MM"
    weather_stop_rain_mm_h: float = 20.0
    weather_stop_visibility_m: float = 50.0
    weather_stop_wind_kmh: float = 60.0
    machine_fault_hold_s: int = 120
    idle_dev_ratio: float = 2.0
    idle_dev_robust_z: float = 2.5
    idle_dev_persist_windows: int = 2
    idle_baseline_min_n: int = 20

    # Anomaly detection (ARCH 6.7)
    anomaly_threshold_pct: float = 0.97
    anomaly_min_train_rows: int = 500
    robust_z_threshold: float = 3.5

    @property
    def parsed_machine_hmac_keys(self) -> dict[str, str]:
        if not self.machine_hmac_keys.strip():
            return {}
        return dict(pair.split(":") for pair in self.machine_hmac_keys.split(","))

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
