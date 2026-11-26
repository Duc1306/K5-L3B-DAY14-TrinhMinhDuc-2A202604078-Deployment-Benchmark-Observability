"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường (tiêu chí 9).

Không có secret nào trong code. Giá trị mặc định chỉ dành cho chạy local;
trên cloud mọi biến được đặt trên platform (Railway / Render).
"""
import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} phải là số nguyên, nhận được {raw!r}") from e


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} phải là số, nhận được {raw!r}") from e


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: _env_str("ENVIRONMENT", "development"))
    app_version: str = field(default_factory=lambda: _env_str("APP_VERSION", "1.0.0"))
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())

    # Secret — KHÔNG có giá trị mặc định
    agent_api_key: str = field(default_factory=lambda: _env_str("AGENT_API_KEY"))

    redis_url: str = field(default_factory=lambda: _env_str("REDIS_URL", "redis://localhost:6379/0"))

    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    def validate(self) -> "Settings":
        """Fail fast: cấu hình sai thì không cho app khởi động."""
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE phải > 0")
        if self.daily_budget_usd <= 0 or self.monthly_budget_usd <= 0:
            raise ValueError("DAILY_BUDGET_USD và MONTHLY_BUDGET_USD phải > 0")
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY bắt buộc khi ENVIRONMENT=production")
        return self


settings = Settings().validate()
