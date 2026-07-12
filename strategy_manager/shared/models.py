from sqlalchemy import (ARRAY, Boolean, Column, Date, DateTime, Float,
                        ForeignKey, Integer, Numeric, String, Text, create_engine,
                        func, Enum, DECIMAL, JSON)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, sessionmaker, declarative_base
import uuid
import datetime
import os
from pytz import timezone
from dotenv import load_dotenv

load_dotenv()

NAME     = os.getenv("DB_NAME",     os.getenv("NAME",     "Kronos"))
USER     = os.getenv("DB_USER",     "postgres")
PASSWORD = os.getenv("DB_PASSWORD", os.getenv("PASSWORD", "kronos123"))
HOST     = os.getenv("DB_HOST",     os.getenv("HOST",     "127.0.0.1"))
PORT     = os.getenv("DB_PORT",     os.getenv("PORT",     "5432"))
SSLMODE  = os.getenv("DB_SSLMODE", "require" if HOST.endswith("tsdb.cloud.timescale.com") else "prefer")

database_connection_string = f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{NAME}?sslmode={SSLMODE}"

# `engine` / `Session` are created lazily (module __getattr__, PEP 562) so that
# importing the model classes alone (tests, offline tools) never constructs a
# connection pool. `from shared.models import Session` keeps working unchanged:
# the first access builds the engine + sessionmaker once and reuses them.
_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_connection_string, pool_size=60, max_overflow=10)
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=_get_engine())
    return _session_factory


def __getattr__(name):
    if name == "engine":
        return _get_engine()
    if name == "Session":
        return _get_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


Base       = declarative_base()
APP_PREFIX = "apis"
IST        = timezone("Asia/Kolkata")


def get_kolkata_time():
    return datetime.datetime.now(IST)


class BaseModel(Base):
    __abstract__ = True
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    created_at  = Column(DateTime(timezone=True), default=get_kolkata_time)
    modified_at = Column(DateTime(timezone=True), default=get_kolkata_time, onupdate=get_kolkata_time)


# ──────────────────────────────────────────────────────────────────────────────

class User(BaseModel):
    __tablename__ = f"{APP_PREFIX}_user"

    email               = Column(String, unique=True)
    first_name          = Column(String(500))
    last_name           = Column(String(500))
    is_active           = Column(Boolean, default=True)
    is_staff            = Column(Boolean, default=False)
    date_joined         = Column(DateTime, default=get_kolkata_time)
    balance             = Column(Numeric(25, 2), default=100000)
    username            = Column(String(30))
    profile_image       = Column(String(1000), default="Profile_image/profile.jpg")
    profile_description = Column(String(500), default="")
    otp_token           = Column(String(256), default="")

    apis_userbrokers = relationship("UserBroker", back_populates="apis_user")

    def __repr__(self):
        return self.email


class Broker(BaseModel):
    __tablename__ = f"{APP_PREFIX}_broker"

    name        = Column(String(50))
    base_url    = Column(String(50))
    instruments = Column(String(5000000), default="{}")
    logo        = Column(String(1000), default="Images/Broker_logo/broker.jpg")

    def __repr__(self):
        return self.name


class UserBroker(BaseModel):
    __tablename__ = f"{APP_PREFIX}_userbroker"

    # default must be a callable — a bare str(uuid.uuid4()) would be evaluated
    # once at import and hand every inserted row the SAME key (unique violation).
    api_key          = Column(String(500), unique=True, default=lambda: str(uuid.uuid4()))
    margin_available = Column(String(100), default="")
    margin_used      = Column(String(100), default="0.00")
    meta_account_id    = Column(String(120), default="")
    meta_api_token_enc = Column(Text, default="")
    status           = Column(String(100), default="ACTIVE")
    is_active        = Column(Boolean, default=True)
    last_updated     = Column(DateTime, default=get_kolkata_time)

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_user.id", ondelete="CASCADE"),
        nullable=False,
    )

    apis_user        = relationship("User",         back_populates="apis_userbrokers")
    apis_userstrategys = relationship("UserStrategy", back_populates="apis_userbroker")
    apis_orders      = relationship("Order",        back_populates="apis_userbroker")

    def __repr__(self):
        return str(self.id)


class CurrencyPair(BaseModel):
    __tablename__ = f"{APP_PREFIX}_currencypair"

    symbol    = Column(String(100), default="XRPUSDT", unique=True)
    name      = Column(String(100), default="XRPUSDT")
    ltp       = Column(String(100), default="0.00")
    tick_size = Column(Numeric(25, 6), default=0.00)
    is_active = Column(Boolean, default=True)

    apis_strategys = relationship("Strategy", back_populates="apis_currencypair")
    apis_positions = relationship("Position", back_populates="apis_currencypair")

    def __repr__(self):
        return self.symbol


class Signal(BaseModel):
    __tablename__ = f"{APP_PREFIX}_signal"

    symbol      = Column(String(50))
    exchange    = Column(String(50), default="NFO")
    price       = Column(Numeric(25, 2), default=0.00)
    description = Column(String(500))
    side        = Column(String(50), default="BUY")
    type        = Column(String(50), default="CALL")

    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_strategy.id", ondelete="CASCADE"),
    )
    apis_strategy = relationship("Strategy", back_populates="apis_signals")

    def __repr__(self):
        return self.symbol + ":" + str(self.strategy_id)


class Strategy(BaseModel):
    __tablename__ = f"{APP_PREFIX}_strategy"

    name             = Column(String(500), unique=True)
    description      = Column(String(500), default="")
    is_active        = Column(Boolean, default=True)
    capital_required = Column(String(100), default="100000.00")
    json_data        = Column(JSON, default={})
    params           = Column(JSON, default={})
    entry_quantity   = Column(Numeric(25, 2), default=0.00)

    currencypair_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_currencypair.id"),
    )

    apis_currencypair  = relationship("CurrencyPair", back_populates="apis_strategys")
    apis_signals       = relationship("Signal",       back_populates="apis_strategy")
    apis_actions       = relationship("Action",       back_populates="apis_strategy")
    apis_userstrategys = relationship("UserStrategy", back_populates="apis_strategy")

    def __repr__(self):
        return self.name


class UserStrategy(BaseModel):
    __tablename__ = f"{APP_PREFIX}_userstrategy"

    name       = Column(String(100), default="User Strategy")
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=get_kolkata_time)
    multiplyer = Column(Integer, default=1)
    deployed   = Column(Boolean, default=False)
    archived   = Column(Boolean, default=False, nullable=False)  # mirror of Django migration 0006

    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_strategy.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_broker_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_userbroker.id", ondelete="CASCADE"),
        nullable=True,
    )

    apis_strategy   = relationship("Strategy",   back_populates="apis_userstrategys")
    apis_userbroker = relationship("UserBroker", back_populates="apis_userstrategys")
    apis_positions  = relationship("Position",   back_populates="apis_userstrategy")

    def __repr__(self):
        return self.name + " " + str(self.id)


class Position(BaseModel):
    __tablename__ = f"{APP_PREFIX}_position"

    avg_buy_price        = Column(Numeric(25, 2), default=0.00)
    avg_sell_price       = Column(Numeric(25, 2), default=0.00)
    total_buy_quantity   = Column(Numeric(25, 2), default=0.00)
    symbol               = Column(String(50))
    quantity             = Column(Numeric(25, 2), default=0.00)
    profit_loss          = Column(Numeric(25, 2), default=0.00)
    profit_loss_percentage = Column(Numeric(25, 2), default=0.00)
    ltp                  = Column(Numeric(25, 2), default=0.00)
    realized_profit_loss = Column(Numeric(25, 2), default=0.00)

    user_strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_userstrategy.id", ondelete="CASCADE"),
    )
    currencypair_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_currencypair.id", ondelete="CASCADE"),
    )

    apis_userstrategy = relationship("UserStrategy", back_populates="apis_positions")
    apis_currencypair = relationship("CurrencyPair", back_populates="apis_positions")
    apis_triggers     = relationship("Trigger",      back_populates="apis_position")
    apis_orders       = relationship("Order",        back_populates="apis_position")

    def __repr__(self):
        return self.symbol


class Action(BaseModel):
    __tablename__ = f"{APP_PREFIX}_action"

    ACTION = [("BUY", "BUY"), ("SELL", "SELL")]

    ACTION_TYPE = (
        ("STOPLOSS",          "STOPLOSS"),
        ("TARGET",            "TARGET"),
        ("REPAIR",            "REPAIR"),
        ("TRAILING_STOPLOSS", "TRAILING_STOPLOSS"),
        ("EXIT",              "EXIT"),
        ("BUY_EXIT",          "BUY_EXIT"),
        ("SELL_EXIT",         "SELL_EXIT"),
    )

    TRIGGER_TYPE = (
        ("POINTS",                    "POINTS"),
        ("PERCENTAGE",                "PERCENTAGE"),
        ("CUMULATIVE_PNL_VALUE",      "CUMULATIVE_PNL_VALUE"),
        ("CUMULATIVE_PNL_PERCENTAGE", "CUMULATIVE_PNL_PERCENTAGE"),
        ("INDEX_POINTS",              "INDEX_POINTS"),
        ("TIME_PERIOD",               "TIME_PERIOD"),
        ("INDEX_PERCENTAGE",          "INDEX_PERCENTAGE"),
        ("CUSTOM",                    "CUSTOM"),
    )

    TRAILING_TYPE = (
        ("PERCENTAGE",           "PERCENTAGE"),
        ("POINTS",               "POINTS"),
        ("CUMULATIVE_PNL_VALUE", "CUMULATIVE_PNL_VALUE"),
    )

    action        = Column(Enum(*[c[0] for c in ACTION],        name="ACTION"),        default="SELL")
    quantity      = Column(Numeric(25, 2), default=0.00)
    trigger_value = Column(Numeric(25, 2), default=0.00)
    trigger_type  = Column(Enum(*[c[0] for c in TRIGGER_TYPE],  name="TRIGGER_TYPE"),  default="PERCENTAGE")
    action_type   = Column(Enum(*[c[0] for c in ACTION_TYPE],   name="ACTION_TYPE"),   default="ENTRY")
    create_trigger = Column(Boolean, default=False)
    trail_type    = Column(Enum(*[c[0] for c in TRAILING_TYPE], name="TRAILING_TYPE"), default="PERCENTAGE")
    trail_value   = Column(Numeric(25, 2), default=0.00)

    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_strategy.id", ondelete="CASCADE"),
    )
    apis_strategy = relationship("Strategy", back_populates="apis_actions")

    def __repr__(self):
        return self.action + ":" + str(self.strategy_id)


class Trigger(BaseModel):
    __tablename__ = f"{APP_PREFIX}_trigger"

    TRIGGER_TYPE = [
        ("TARGET",                    "TARGET"),
        ("STOPLOSS",                  "STOPLOSS"),
        ("SUM_STOPLOSS",              "SUM_STOPLOSS"),
        ("SUM_TARGET",                "SUM_TARGET"),
        ("CUSTOM",                    "CUSTOM"),
        ("TRAILING_STOPLOSS_POINTS",  "TRAILING_STOPLOSS_POINTS"),
        ("TRAILING_STOPLOSS_SUM",     "TRAILING_STOPLOSS_SUM"),
        ("ENTRY",                     "ENTRY"),
        ("EXIT",                      "EXIT"),
    ]

    STATUS = [
        ("PENDING",   "PENDING"),
        ("TRIGGERED", "TRIGGERED"),
        ("CANCELLED", "CANCELLED"),
    ]

    date          = Column(Date, default=get_kolkata_time)
    symbol        = Column(String(50), default="")
    trigger_price = Column(Numeric(25, 2), default=0.00)
    order_type    = Column(String(50), default="LIMIT")
    side          = Column(String(50), default="BUY")
    greater_than  = Column(Boolean, default=True)
    quantity      = Column(Numeric(25, 2), default=0.00)
    trigger_type  = Column(Enum(*[c[0] for c in TRIGGER_TYPE], name="trigger_type"), default="STOPLOSS")
    check_at_broker  = Column(Boolean, default=False)
    broker_order_id  = Column(String(100), default="")
    status        = Column(Enum(*[c[0] for c in STATUS], name="status"), default="PENDING")
    trail_value   = Column(Numeric(25, 2), default=0.00)
    trail_points  = Column(Numeric(25, 2), default=0.00)

    position_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_position.id", ondelete="CASCADE"),
    )
    apis_position = relationship("Position", back_populates="apis_triggers")

    def __repr__(self):
        return self.symbol + " " + str(self.date)


class Order(BaseModel):
    __tablename__ = f"{APP_PREFIX}_order"

    symbol          = Column(String(50), default="")
    exchange        = Column(String(50), default="NFO")
    price           = Column(Numeric(25, 2), default=0.00)
    condition       = Column(String(50), default="ENTRY")
    side            = Column(String(50), default="BUY")
    quantity        = Column(Numeric(25, 2), default=0.00)
    amount          = Column(Numeric(25, 2), default=0.00)
    order_type      = Column(String(50), default="MARKET")
    status          = Column(String(50), default="PENDING")
    reason          = Column(String(200), default="NONE")
    broker_order_id = Column(String(100), default="")

    position_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_position.id", ondelete="CASCADE"),
    )
    user_broker_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_userbroker.id", ondelete="CASCADE"),
    )

    apis_position   = relationship("Position",   back_populates="apis_orders")
    apis_userbroker = relationship("UserBroker", back_populates="apis_orders")

    def __repr__(self):
        return self.symbol + " " + self.condition


class StrategySignal(BaseModel):
    """One row per signal generated by a strategy runner.

    Logged inside `entry_manager.place_entry()`. Status transitions:
      FIRED    -> initial write, before MetaAPI call
      PLACED   -> MetaAPI accepted, position_id set
      REJECTED -> place_entry returned False, rejection_reason set

    A strategy that's silent for a session produces 0 rows — useful signal
    on its own. A strategy that's firing-but-rejected shows up as REJECTED
    rows so you can tell "no signals" apart from "all signals filtered out".
    """
    __tablename__ = f"{APP_PREFIX}_strategysignal"

    symbol           = Column(String(50), nullable=False)
    side             = Column(String(10), nullable=False)
    entry_price      = Column(Numeric(25, 5), nullable=False)
    stop_loss        = Column(Numeric(25, 5))
    take_profit      = Column(Numeric(25, 5))
    reason           = Column(String(500))
    status           = Column(String(30), nullable=False, default="FIRED")
    rejection_reason = Column(String(500))
    signal_at        = Column(DateTime(timezone=True), default=get_kolkata_time, nullable=False)

    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_strategy.id", ondelete="CASCADE"),
        nullable=False,
    )
    position_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_position.id", ondelete="SET NULL"),
        nullable=True,
    )

    apis_strategy = relationship("Strategy")
    apis_position = relationship("Position")

    def __repr__(self):
        return f"{self.symbol}:{self.side}@{self.entry_price}:{self.status}"


class RegimeSnapshot(BaseModel):
    """One row per Strategy Manager tick (~60s): the market regime as computed
    by strategies/regime/regime_engine.py. Time-series — query latest by
    (symbol, created_at). Django owns the schema (apis.RegimeSnapshot)."""
    __tablename__ = f"{APP_PREFIX}_regimesnapshot"

    symbol        = Column(String(20), nullable=False)
    d1_bias       = Column(String(10))    # bullish | bearish | ranging
    h4_bias       = Column(String(10))    # long | short | neutral
    vol_regime    = Column(String(10))    # LOW | NORMAL | HIGH | EXTREME
    trend_regime  = Column(String(10))    # TRENDING | RANGING | MIXED
    session       = Column(String(10))    # ASIA | LONDON | NY | OVERLAP | ROLLOVER
    market_closed = Column(Boolean, default=False)
    details       = Column(JSON, default={})   # raw numbers (ATR, ER, pctl, ...)

    def __repr__(self):
        return f"{self.symbol}:{self.trend_regime}/{self.vol_regime}@{self.created_at}"


class ManagedStrategy(BaseModel):
    """A UserStrategy placed under Strategy Manager control. The manager flips
    UserStrategy.is_active per the gating policy — but ONLY when arm_mode != OFF.
    arm_mode is user-owned (set via the backend API); the manager never writes it."""
    __tablename__ = f"{APP_PREFIX}_managedstrategy"

    slot              = Column(String(20))                 # trend | session | momentum | scalper
    policy_key        = Column(String(40))                 # always_on | session_vol | trending | quiet_fade
    policy_params     = Column(JSON, default={})
    arm_mode          = Column(String(5), default="OFF")   # OFF | PAPER | LIVE
    live_eligible     = Column(Boolean, default=False)
    desired_active    = Column(Boolean, default=False)     # manager's last verdict
    last_reason       = Column(String(300), default="")
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)

    user_strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_userstrategy.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    apis_userstrategy = relationship("UserStrategy")

    def __repr__(self):
        return f"{self.slot}:{self.policy_key}:{self.arm_mode}"


class ManagerConfig(BaseModel):
    """Single-row global Strategy Manager config. master_mode=OFF (the deploy
    default) means the manager records regime snapshots but flips nothing."""
    __tablename__ = f"{APP_PREFIX}_managerconfig"

    master_mode              = Column(String(3), default="OFF")       # OFF | ON
    kill_switch_loss_usd     = Column(Numeric(10, 2), default=150.00)
    max_concurrent_positions = Column(Integer, default=3)
    state                    = Column(JSON, default={})               # e.g. kill_tripped_date

    def __repr__(self):
        return f"master={self.master_mode}"


class ManagerAction(BaseModel):
    """Audit trail: one row per manager state transition (never per tick)."""
    __tablename__ = f"{APP_PREFIX}_manageraction"

    action = Column(String(15))            # START | PAUSE | KILL_SWITCH | INFO
    reason = Column(String(300))
    regime = Column(JSON, default={})      # summary copy of the snapshot at decision time

    managed_strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_managedstrategy.id", ondelete="SET NULL"),
        nullable=True,
    )
    apis_managedstrategy = relationship("ManagedStrategy")

    def __repr__(self):
        return f"{self.action}:{self.reason}"


class BacktestReport(BaseModel):
    __tablename__ = f"{APP_PREFIX}_backtestreport"

    run_label       = Column(String(200), nullable=False)
    period_start    = Column(Date)
    period_end      = Column(Date)
    trades          = Column(Integer, default=0, nullable=False)
    wins            = Column(Integer, default=0, nullable=False)
    losses          = Column(Integer, default=0, nullable=False)
    win_rate_pct    = Column(Numeric(8, 4))
    pnl_pts         = Column(Numeric(18, 4))
    max_dd_pts      = Column(Numeric(18, 4))
    avg_win_pts     = Column(Numeric(18, 4))
    avg_loss_pts    = Column(Numeric(18, 4))
    profit_factor   = Column(Numeric(12, 4))
    expectancy_pts  = Column(Numeric(18, 6))
    sharpe_daily    = Column(Numeric(12, 4))
    source_csv      = Column(String(500))
    params_snapshot = Column(JSON, default={})
    notes           = Column(String)

    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_PREFIX}_strategy.id", ondelete="CASCADE"),
        nullable=False,
    )

    apis_strategy = relationship("Strategy")

    def __repr__(self):
        return f"{self.run_label}:{self.strategy_id}"
