"""Minimal SQLAlchemy declarative models for Phase 0.

Just enough for Alembic to have something to migrate against. Later phases
add the full table list from the plan's "Database" section (order_events,
trades, cycles, approvals, account_snapshots, ml_predictions, trial_ledger,
instruments, etc).
"""

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EngineState(Base):
    """Single-row(ish) table holding mode/run-state/halt flags — persisted,
    not in-memory, so a restart can't silently reset a halt."""

    __tablename__ = "engine_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )


class BarIngestLog(Base):
    """Read-side ORM mirror of `te.data.ingest_log.bar_ingest_log` (the
    physical table `te/data/ingest_log.py` writes to via a plain
    `sqlalchemy.Core` `Table`, to keep `te.data` out of `te.persistence` per
    the layer rule). Declared here so later phases' repos/dashboard
    endpoints have an ORM-mapped read path. Keep the two column lists in
    sync by hand — see `te/data/ingest_log.py`'s module docstring."""

    __tablename__ = "bar_ingest_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[dt.date] = mapped_column(nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Instrument(Base):
    """Read-side ORM mirror of `te.broker.instrument_sync.instruments` (see
    that module's docstring for why the physical table is written via a
    separate `sqlalchemy.Core` `Table` rather than through this class)."""

    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(8), nullable=False)
    expiry: Mapped[str] = mapped_column(String(16), nullable=False)
    strike: Mapped[float] = mapped_column(nullable=False)
    lot_size: Mapped[int] = mapped_column(nullable=False)
    tick_size: Mapped[float] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="openalgo")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OptionBhav(Base):
    """Read-side ORM mirror of `te.data.bhav_store.option_bhav` (see that
    module's docstring — this is the actual historical bhavcopy DATA table,
    distinct from `BarIngestLog`'s audit-only row counts). Keep the two
    column lists in sync by hand, same convention as `Instrument`/
    `BarIngestLog` above."""

    __tablename__ = "option_bhav"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_date: Mapped[dt.date] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    expiry: Mapped[dt.date] = mapped_column(nullable=False)
    strike: Mapped[float] = mapped_column(nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    open: Mapped[float] = mapped_column(nullable=False)
    high: Mapped[float] = mapped_column(nullable=False)
    low: Mapped[float] = mapped_column(nullable=False)
    close: Mapped[float] = mapped_column(nullable=False)
    settle_price: Mapped[float] = mapped_column(nullable=False)
    open_interest: Mapped[int] = mapped_column(nullable=False)
    change_in_oi: Mapped[int] = mapped_column(nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "exchange", "symbol", "expiry", "strike", "option_type", name="uq_option_bhav_contract_day"
        ),
    )


class OrderEventRow(Base):
    """Append-only source of truth for Phase 3's execution core — see
    `te/execution/store.py`. Never UPDATEd or DELETEd; `Order`/fill/position
    state is always a pure `fold()` over the rows for one `client_order_id`,
    read back in `seq` order. `UNIQUE(client_order_id, seq)` makes a
    duplicate append (e.g. two concurrent writers racing) fail loudly rather
    than silently reordering history.

    `orders`/`fills`/`positions` projection tables are DEFERRED this phase —
    the fold-based read path (`OrderEventStore.fold_order()`) is the
    non-negotiable core the plan calls out; rebuildable projection tables are
    a later-phase addition once there's a concrete read-heavy consumer
    (dashboard endpoints) that needs them, per the plan's "Database" section
    ("orders/fills/positions as rebuildable projections").
    """

    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("client_order_id", "seq", name="uq_order_events_client_order_id_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CycleRow(Base):
    """One engine decision cycle — the `cycles` table from the plan's
    "Database" section. `cycle_evaluations`/`evaluation_conditions` feed
    Today Decisions on the dashboard."""

    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)


class CycleEvaluationRow(Base):
    """One `te.domain.evaluation.Evaluation` (one strategy x one instrument
    x one cycle). `evaluation_id` is the natural key used to join
    `evaluation_conditions` rows."""

    __tablename__ = "cycle_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class EvaluationConditionRow(Base):
    """One `te.domain.evaluation.ConditionResult`, persisted verbatim
    (including `actual`) — never re-derived/approximated on read, per the
    decision-explainability requirement."""

    __tablename__ = "evaluation_conditions"
    __table_args__ = (Index("ix_evaluation_conditions_evaluation_id", "evaluation_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seq: Mapped[int] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    required: Mapped[str] = mapped_column(Text, nullable=False)
    actual: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SkippedSignalRow(Base):
    """Every skip — including a zero-lots sizing rejection — gets a row here
    with the REAL reason. Closes the exact bug class the plan calls out:
    the old engine silently never traded."""

    __tablename__ = "skipped_signals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class RiskEventRow(Base):
    """One risk-governance event (daily-loss halt, max-positions block,
    max-trades block, kill-switch trip, ...). Persisted so a halt survives a
    process restart — see `te.risk.limits`/`te.risk.killswitch`."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")


class OpenPositionRow(Base):
    """A currently-open paper position, WITH its mandatory `ExitPlan`
    fields persisted alongside it — there is no row shape here that omits
    stop/target/time-exit, matching `te.engine.exits.OpenPosition`'s
    constructor requiring an `ExitPlan`. `closed_at IS NULL` is what a
    "currently open" position means; `te.risk.limits`'s max-concurrent-
    positions check counts these rows, so it survives a restart."""

    __tablename__ = "open_positions"
    __table_args__ = (Index("ix_open_positions_closed_at", "closed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    lots: Mapped[int] = mapped_column(nullable=False)
    lot_size: Mapped[int] = mapped_column(nullable=False)
    entry_premium_paise: Mapped[int] = mapped_column(nullable=False)
    stop_paise: Mapped[int] = mapped_column(nullable=False)
    current_stop_paise: Mapped[int] = mapped_column(nullable=False)
    trailing_distance_paise: Mapped[int | None] = mapped_column(nullable=True)
    target_paise: Mapped[int] = mapped_column(nullable=False)
    max_hold_seconds: Mapped[int] = mapped_column(nullable=False)
    hard_exit_by: Mapped[str] = mapped_column(String(8), nullable=False)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeRow(Base):
    """A closed round-trip trade. NO bare `pnl` column, per the plan's
    net-ness enforcement — gross, costs, and net are three separate columns
    so nothing can be accidentally serialised as an unlabelled gross
    figure."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_closed_at", "closed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    lots: Mapped[int] = mapped_column(nullable=False)
    lot_size: Mapped[int] = mapped_column(nullable=False)
    entry_premium_paise: Mapped[int] = mapped_column(nullable=False)
    exit_premium_paise: Mapped[int] = mapped_column(nullable=False)
    gross_pnl_paise: Mapped[int] = mapped_column(nullable=False)
    costs_paise: Mapped[int] = mapped_column(nullable=False)
    net_pnl_paise: Mapped[int] = mapped_column(nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- post-hoc trade-review fields (nullable: pre-existing rows have none) ---

    #: The stop and target the position was OPENED with, copied off
    #: `OpenPositionRow` at close time. `stop_paise` is the ORIGINAL stop, not
    #: the trailed `current_stop_paise`. Without these, "what were we actually
    #: risking on this trade?" is unanswerable from the trade record alone
    #: once the `open_positions` row is closed out.
    stop_paise: Mapped[int | None] = mapped_column(nullable=True)
    target_paise: Mapped[int | None] = mapped_column(nullable=True)

    #: Back-reference to the `cycle_evaluations` row whose firing produced
    #: this trade, so a closed trade can be traced to the exact
    #: condition-by-condition evaluation behind it.
    #:
    #: **Currently never populated.** `OpenPositionRow` carries no evaluation
    #: reference, so threading one from `run_entry_cycle` through to
    #: `run_exit_cycle`'s close path needs a matching column on
    #: `open_positions` too — a wider change than this column. The column
    #: exists (nullable) so that later change is a pure backfill rather than
    #: another migration of the trades table.
    cycle_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("cycle_evaluations.id"), nullable=True
    )


class ApprovalRow(Base):
    """Persisted `PendingApproval` lifecycle — backs `te/engine/approvals.py`
    and the `/api/approvals` router now that Phase 4 has real data."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    lots: Mapped[int] = mapped_column(nullable=False)
    premium_paise: Mapped[int] = mapped_column(nullable=False)
    stop_paise: Mapped[int] = mapped_column(nullable=False)
    target_paise: Mapped[int] = mapped_column(nullable=False)
    estimated_cost_paise: Mapped[int] = mapped_column(nullable=False)
    model_verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class TrialLedgerRow(Base):
    """Read-side ORM mirror of `te.ml.trials.trial_ledger` (see that
    module's docstring for why the physical table is written via a
    separate `sqlalchemy.Core` `Table` rather than through this class, and
    for why this table has no delete path anywhere in the codebase — it is
    the structural guarantee behind `DeflatedSharpeRatio`'s honest trial
    count)."""

    __tablename__ = "trial_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sharpe: Mapped[float] = mapped_column(nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)


class MlMaturityState(Base):
    """Read-side ORM mirror of `te.ml.gates.ml_maturity_state` (see that
    module's docstring for why the physical table is written via a
    separate `sqlalchemy.Core` `Table`, not through this class) — the
    single-row current ML maturity stage, read from the DB, never a config
    file."""

    __tablename__ = "ml_maturity_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelPromotionRow(Base):
    """Read-side ORM mirror of `te.ml.gates.model_promotions` — the audit
    trail for every stage transition a model has ever undergone."""

    __tablename__ = "model_promotions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    criteria_json: Mapped[str] = mapped_column(Text, nullable=False, default="")


class MlPredictionRow(Base):
    """Read-side ORM mirror of `te.ml.gates.ml_predictions` — every
    shadow-or-higher prediction ever logged. Never read by
    `te.engine.cycle`; exists for future promotion-gate evaluation and
    dashboard endpoints."""

    __tablename__ = "ml_predictions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cycle_id: Mapped[int] = mapped_column(nullable=False)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_spec_name: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_spec_version: Mapped[int] = mapped_column(nullable=False)
    p: Mapped[float] = mapped_column(Float, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    displayed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ModelRegistryRow(Base):
    """Read-side ORM mirror of `te.ml.registry.model_registry` — trained
    model artifacts (`artifact_blob`) plus provenance (feature spec
    version, DSR, PBO, honest trial count at training time, calibration
    info)."""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    feature_spec_name: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_spec_version: Mapped[int] = mapped_column(nullable=False)
    artifact_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    gbm_library: Mapped[str] = mapped_column(String(16), nullable=False)
    calibration_method: Mapped[str] = mapped_column(String(16), nullable=False)
    calibration_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    dsr: Mapped[float] = mapped_column(Float, nullable=False)
    pbo: Mapped[float] = mapped_column(Float, nullable=False)
    n_trials_at_training: Mapped[int] = mapped_column(nullable=False)
    n_labeled_samples: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SlippageObservationRow(Base):
    """One Tier-0 slippage observation — see `te.risk.monitors.SlippageMonitor`.
    `diff_paise = actual_paise - expected_paise`; positive means the fill
    cost MORE than `te.domain.costs.CostModel` + the order's requested price
    modelled, i.e. slippage working against us. Persisted (not an in-memory
    rolling window) so the window survives a process restart."""

    __tablename__ = "slippage_observations"
    __table_args__ = (Index("ix_slippage_observations_instrument_id", "instrument", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_paise: Mapped[int] = mapped_column(nullable=False)
    actual_paise: Mapped[int] = mapped_column(nullable=False)
    diff_paise: Mapped[int] = mapped_column(nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")


class MonitorStateRow(Base):
    """Persisted state for one `te.risk.monitors` stateful monitor instance
    (currently only `CusumMonitor`'s S+/S-), keyed by an arbitrary `key` a
    caller chooses (e.g. `"cusum:orb"`), so a process restart doesn't
    silently reset an in-progress breach — the plan's explicit requirement
    for `monitor_state`."""

    __tablename__ = "monitor_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    s_pos: Mapped[float] = mapped_column(Float, nullable=False)
    s_neg: Mapped[float] = mapped_column(Float, nullable=False)
    last_action: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestDrawdownEnvelopeRow(Base):
    """One percentile of a `te.backtest.report.stationary_bootstrap_drawdown_envelope()`
    result, computed ONCE per backtest run and persisted here —
    `te.risk.monitors.DrawdownEnvelope` (Tier 2) reads this table and never
    re-runs the bootstrap live. `(run_id, percentile)` identifies one row;
    a run typically stores a handful of percentiles (e.g. 0.5/0.95/0.99)."""

    __tablename__ = "backtest_drawdown_envelopes"
    __table_args__ = (UniqueConstraint("run_id", "percentile", name="uq_backtest_drawdown_envelopes_run_percentile"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    percentile: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown_paise: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """Append-only audit trail — every state-changing action, human or
    engine-initiated, gets a row here."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
