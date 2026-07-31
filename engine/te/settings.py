"""Application settings — pydantic-settings, env_prefix="TE_".

`env` and `database_url` have NO defaults: a missing environment variable
must raise a `ValidationError` at instantiation, never silently fall back to
a default (a silent default is how tests end up writing into prod — see the
plan's "Database" section).
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    #: Deliberately NO `env_file` here — see the module docstring's "NO
    #: defaults" rule. A silent fallback file would defeat the exact
    #: `test_settings_fails_without_*` tests that rule exists for. The
    #: bare-host dev engine (`yarn dev:engine`/`dev:all`) instead loads
    #: `engine/.env` at the SHELL level (`dotenv run --`) before this class
    #: ever runs, so those values arrive as real process env vars — the same
    #: mechanism Docker Compose uses, not a second, quieter source Settings
    #: itself falls back to.
    model_config = SettingsConfigDict(env_prefix="TE_", extra="ignore")

    env: Literal["dev", "prod"]
    database_url: str

    bar_store_path: Path
    openalgo_host: str

    #: OpenAlgo's WebSocket endpoint, e.g. `ws://openalgo:8765`. Configured
    #: EXPLICITLY and separately from `openalgo_host` — deliberately never
    #: derived from it. `openalgo_host` already carries its own port
    #: (`http://openalgo:5000`), so scheme-swapping string surgery plus a
    #: `:8765` suffix yields the invalid two-port `ws://openalgo:5000:8765`;
    #: and in a real deployment the REST and WS endpoints may sit on
    #: entirely different hostnames, ports and paths behind a proxy.
    #:
    #: Required, with NO default, for the same reason `env`/`database_url`
    #: are: a silently-wrong endpoint here means the recorder simply never
    #: connects, no bars are ever written, and every strategy quietly
    #: no-ops — a failure that is invisible until someone notices the
    #: missing data. A missing env var must fail loudly at startup instead.
    openalgo_ws_host: str

    openalgo_api_key: SecretStr
    cors_origins: list[str]

    charges_path: Path = Path("config/charges.yaml")
    max_orders_per_second: int = 5

    # Paper-trading cycle job (te.engine.scheduler) — an intraday ORB
    # strategy, so the default interval is short. `paper_cycle_instruments`
    # empty means the job is registered but is a documented per-instrument
    # no-op (never a hardcoded literal instrument list) until configured.
    paper_cycle_enabled: bool = True
    paper_cycle_interval_minutes: int = 1
    paper_cycle_instruments: tuple[str, ...] = ()
    paper_cycle_exchange: str = "NFO"
    paper_cycle_strategy: str = "orb"
    paper_cycle_lot_size: int = 65
    paper_cycle_capital_paise: int = 2_000_000
    paper_cycle_risk_budget_pct: Decimal = Decimal(2)
    paper_cycle_min_edge_multiple: Decimal = Decimal("1.2")
    paper_cycle_stop_distance_paise: int = 700
    paper_cycle_target_distance_paise: int = 1_500
    paper_cycle_trailing_distance_paise: int | None = 300
    paper_cycle_max_hold_minutes: int = 180
    paper_cycle_hard_exit_by: dt.time = dt.time(15, 20)
    paper_cycle_max_daily_loss_paise: int = 1_000_000
    paper_cycle_max_concurrent_positions: int = 5
    paper_cycle_max_trades_per_day: int = 20

    # --- Option contract resolution (see te/engine/contract.py) ---
    #: Strike offset handed to OpenAlgo's `optionsymbol` service: `ATM`,
    #: `OTM1`..`OTM20`, `ITM1`..`ITM20`. ATM (delta ~0.5) is the standard
    #: intraday choice — tightest spreads, most linear response to the
    #: underlying. Deep OTM is the classic small-capital trap: cheap premium
    #: buys a low delta, so a correct directional call still loses to costs.
    paper_cycle_option_offset: str = "ATM"
    #: Stop/target as a % of the OPTION premium. These take priority over the
    #: absolute `*_distance_paise` values above, which are index-point-scaled
    #: leftovers and mean different things at different premium levels.
    paper_cycle_stop_pct: Decimal | None = Decimal(20)
    paper_cycle_target_pct: Decimal | None = Decimal(40)
    #: Reject a resolved contract whose bid-ask spread exceeds this % of LTP.
    #: Live NIFTY chain (2026-07-31) runs 0.1-0.4% through OTM5 and widens to
    #: ~1.1% by OTM8, so 1.0% admits the liquid band and excludes the rest.
    paper_cycle_max_spread_pct: Decimal = Decimal(1)
    #: Reject a contract quoting below this premium — a near-zero premium is a
    #: dead/untraded strike where the fixed ₹40 brokerage alone dominates.
    paper_cycle_min_premium_paise: int = 500

    # Automated daily OpenAlgo-app + Angel-broker relogin (te.broker.openalgo_login)
    # — Angel expires its broker session nightly regardless of restarts; ALL
    # FIVE optional so every existing `Settings()` call site (tests included)
    # keeps working unchanged. `_run_openalgo_relogin` simply skips with a
    # logged reason when any is unset, same convention as
    # `paper_cycle_instruments`'s empty-tuple no-op above.
    openalgo_app_username: str | None = None
    openalgo_app_password: SecretStr | None = None
    angel_client_id: str | None = None
    angel_pin: SecretStr | None = None
    angel_totp_secret: SecretStr | None = None

    @field_validator(
        "openalgo_app_username",
        "openalgo_app_password",
        "angel_client_id",
        "angel_pin",
        "angel_totp_secret",
        mode="before",
    )
    @classmethod
    def _blank_relogin_field_is_unset(cls, value: object) -> object:
        """Docker Compose's `${VAR:-}` substitution passes an EMPTY STRING,
        not an unset variable, when `VAR` is missing from `.env` — which
        would otherwise satisfy the `str | None` type as `""` rather than
        falling through to the `None` default, silently defeating
        `run_openalgo_relogin`'s all-or-nothing "not configured" check (it
        would see 5 present-but-blank values and attempt a real login with
        empty credentials instead of skipping)."""
        return None if value == "" else value

    @model_validator(mode="after")
    def _cross_env_guard(self) -> Self:
        """Rejects `env="dev"` with "prod" in `database_url` and vice versa —
        catches transposed dev/prod DB files before they can be used."""
        url_lower = self.database_url.lower()
        if self.env == "dev" and "prod" in url_lower:
            raise ValueError(f"TE_ENV=dev but TE_DATABASE_URL looks like a prod database: {self.database_url!r}")
        if self.env == "prod" and "dev" in url_lower:
            raise ValueError(f"TE_ENV=prod but TE_DATABASE_URL looks like a dev database: {self.database_url!r}")
        return self
