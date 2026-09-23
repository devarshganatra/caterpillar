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

    @property
    def parsed_machine_hmac_keys(self) -> dict[str, str]:
        if not self.machine_hmac_keys.strip():
            return {}
        return dict(pair.split(":") for pair in self.machine_hmac_keys.split(","))

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
