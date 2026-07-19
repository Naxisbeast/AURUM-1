"""Configuration helpers for OBSIDIAN Phase 0.

Credentials are resolved only from environment variables. Config files may
name environment variables, but should not contain secret values.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is listed in requirements.
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class OandaConfig:
    api_key_env: str = "OANDA_API_KEY"
    account_id_env: str = "OANDA_ACCOUNT_ID"
    environment_env: str = "OANDA_ENV"
    default_environment: str = "practice"
    practice_url: str = "https://api-fxpractice.oanda.com"
    live_url: str = "https://api-fxtrade.oanda.com"

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

    @property
    def account_id(self) -> str | None:
        return os.getenv(self.account_id_env)

    @property
    def environment(self) -> str:
        return os.getenv(self.environment_env, self.default_environment).strip().lower() or "practice"

    @property
    def base_url(self) -> str:
        return self.live_url if self.environment == "live" else self.practice_url


@dataclass(frozen=True)
class ObsidianConfig:
    db_path: Path = Path("data/obsidian_market_cache.sqlite3")
    default_instrument: str = "XAU_USD"
    default_timeframe: str = "M15"
    request_timeout_seconds: float = 30.0
    oanda: OandaConfig = field(default_factory=OandaConfig)


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: str | Path | None = None) -> ObsidianConfig:
    if path is None:
        return ObsidianConfig()
    raw = _load_mapping(Path(path))
    oanda_raw = dict(raw.get("oanda", {}))
    oanda = OandaConfig(
        api_key_env=str(oanda_raw.get("api_key_env", OandaConfig.api_key_env)),
        account_id_env=str(oanda_raw.get("account_id_env", OandaConfig.account_id_env)),
        environment_env=str(oanda_raw.get("environment_env", OandaConfig.environment_env)),
        default_environment=str(oanda_raw.get("default_environment", OandaConfig.default_environment)),
        practice_url=str(oanda_raw.get("practice_url", OandaConfig.practice_url)),
        live_url=str(oanda_raw.get("live_url", OandaConfig.live_url)),
    )
    return ObsidianConfig(
        db_path=Path(str(raw.get("db_path", ObsidianConfig.db_path))),
        default_instrument=str(raw.get("default_instrument", ObsidianConfig.default_instrument)),
        default_timeframe=str(raw.get("default_timeframe", ObsidianConfig.default_timeframe)),
        request_timeout_seconds=float(raw.get("request_timeout_seconds", ObsidianConfig.request_timeout_seconds)),
        oanda=oanda,
    )


def _load_mapping(path: Path) -> Mapping[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        loaded = yaml.safe_load(raw)
    else:
        loaded = json.loads(raw)
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return loaded
