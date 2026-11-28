import pytest

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    BalanceReference,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import create_account

from openquant.simulation.enums import (
    AccountStatus,
    TerminationReason
)

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)

from openquant.simulation.engine import (
    AccountEngine,
    InvalidAccountStateError,
    EventProcessingError
)

from openquant.simulation.drawdown import (
    DrawdownConfigurationError,
    DrawdownInvariantError,
    compile_drawdown,
    calculate_floor,
    initialize_drawdown,
    get_reference_value,
    is_drawdown_breached,
    assert_drawdown_invariants
)


# ==================================================
# Fixtures and helpers
# ==================================================

@pytest.fixture
def timestamp():

    return datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )


@pytest.fixture
def make_account():

    def factory(
        drawdown_type=DrawdownType.STATIC,
        *,
        amount=1000,
        cap=None,
        reference=BalanceReference.BALANCE,
        breach_reference=BalanceReference.EQUITY,
        breach_on_equal=True
    ):

        drawdown = DrawdownRules(
            type=drawdown_type,
            amount=amount,
            trailing_stop_balance=cap,
            reference=reference,
            breach_reference=breach_reference,
            breach_on_equal=breach_on_equal
        )

        firm = PropFirm(
            name="Example Trading",

            ruleset=Ruleset(
                account_name="25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,
                    drawdown=drawdown,
                    fees=FeeRules()
                )
            )
        )

        revision = FirmRevision(
            firm=firm,
            metadata=RevisionMetadata()
        )

        return create_account(
            "account_001",
            revision,
            SimulationMode.EVALUATION_ONLY,
            require_verified=False
        )

    return factory


@pytest.fixture
def make_engine(make_account, timestamp):

    def factory(
        drawdown_type=DrawdownType.STATIC,
        *,
        strict=True,
        **kwargs
    ):

        account = make_account(
            drawdown_type,
            **kwargs
        )

        engine = AccountEngine(
            account,
            strict=strict
        )

        engine.process(
            SessionStarted(
                timestamp,
                "day_1"
            )
        )

        return engine

    return factory


# ==================================================
# Compiled configuration
# ==================================================

def test_compile_drawdown():

    rules = DrawdownRules(
        DrawdownType.TRAILING_EOD,
        1000,
        trailing_stop_balance=25_100
    )

    compiled = compile_drawdown(rules)

    assert compiled.amount_cents == 100_000

    assert compiled.trailing_cap_cents == 2_510_000

    assert compiled.type is DrawdownType.TRAILING_EOD

    assert compiled.reference is BalanceReference.BALANCE

    assert (
        compiled.breach_reference
        is BalanceReference.EQUITY
    )


def test_compile_no_cap():

    compiled = compile_drawdown(
        DrawdownRules(
            DrawdownType.STATIC,
            1000
        )
    )

    assert compiled.trailing_cap_cents is None


@pytest.mark.parametrize(
    "amount",
    [0, -100, 1000.001, float("inf"), True]
)
def test_invalid_drawdown_amount(amount):

    rules = DrawdownRules(
        DrawdownType.STATIC,
        amount
    )

    with pytest.raises(DrawdownConfigurationError):

        compile_drawdown(rules)


def test_static_drawdown_rejects_cap():

    rules = DrawdownRules(
        DrawdownType.STATIC,
        1000,
        trailing_stop_balance=25_000
    )

    with pytest.raises(DrawdownConfigurationError):

        compile_drawdown(rules)


def test_fractional_cent_cap_rejected():

    rules = DrawdownRules(
        DrawdownType.TRAILING_EOD,
        1000,
        trailing_stop_balance=25_100.001
    )

    with pytest.raises(DrawdownConfigurationError):

        compile_drawdown(rules)


# ==================================================
# Initialization
# ==================================================

@pytest.mark.parametrize(
    "drawdown_type",
    list(DrawdownType)
)
def test_initialize_drawdown(
    make_account,
    drawdown_type
):

    account = make_account(drawdown_type)

    assert account.drawdown_floor_cents is None

    AccountEngine(account)

    assert account.high_water_mark_cents == 2_500_000

    assert account.drawdown_floor_cents == 2_400_000


def test_initial_remaining_drawdown(make_account):

    account = make_account()

    AccountEngine(account)

    assert account.remaining_drawdown_cents == 100_000

    assert account.current_drawdown_cents == 0


def test_cap_below_initial_floor(make_account):

    account = make_account(
        DrawdownType.TRAILING_EOD,
        cap=23_000
    )

    with pytest.raises(
        DrawdownConfigurationError,
        match="initial drawdown floor"
    ):

        AccountEngine(account)


def test_cannot_initialize_twice(make_account):

    account = make_account()

    engine = AccountEngine(account)

    with pytest.raises(DrawdownInvariantError):

        initialize_drawdown(
            account,
            engine.drawdown
        )


def test_partially_initialized_state(make_account):

    account = make_account()

    account.high_water_mark_cents = 2_500_000

    with pytest.raises(DrawdownInvariantError):

        AccountEngine(account)


# ==================================================
# Pure mathematical functions
# ==================================================

def test_uncapped_floor():

    rules = compile_drawdown(
        DrawdownRules(
            DrawdownType.TRAILING_EOD,
            1000
        )
    )

    assert calculate_floor(
        2_600_000,
        rules
    ) == 2_500_000


def test_capped_floor():

    rules = compile_drawdown(
        DrawdownRules(
            DrawdownType.TRAILING_EOD,
            1000,
            trailing_stop_balance=25_100
        )
    )

    assert calculate_floor(
        2_800_000,
        rules
    ) == 2_510_000


def test_reference_values(make_account):

    account = make_account()

    account.unrealized_pnl_cents = -20_000

    assert get_reference_value(
        account,
        BalanceReference.BALANCE
    ) == 2_500_000

    assert get_reference_value(
        account,
        BalanceReference.EQUITY
    ) == 2_480_000


# ==================================================
# Static drawdown
# ==================================================

def test_static_floor_does_not_trail(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(
            timestamp,
            200_000
        )
    )

    account = engine.account

    assert account.balance_cents == 2_700_000

    assert account.high_water_mark_cents == 2_500_000

    assert account.drawdown_floor_cents == 2_400_000


def test_static_survives_above_floor(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -99_999)
    )

    assert engine.account.is_active

    assert engine.account.remaining_drawdown_cents == 1


def test_static_terminates_at_floor(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    assert not engine.account.is_active

    assert (
        engine.account.termination_reason
        is TerminationReason.DRAWDOWN
    )


def test_static_terminates_below_floor(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -150_000)
    )

    assert (
        engine.account.status
        is AccountStatus.TERMINATED
    )

    assert engine.account.balance_cents == 2_350_000


# ==================================================
# Exact boundary behavior
# ==================================================

@pytest.mark.parametrize(
    "loss,breach_on_equal,terminated",
    [
        (99_999, True, False),
        (100_000, True, True),
        (100_001, True, True),

        (99_999, False, False),
        (100_000, False, False),
        (100_001, False, True)
    ]
)
def test_drawdown_boundary(
    make_engine,
    timestamp,
    loss,
    breach_on_equal,
    terminated
):

    engine = make_engine(
        breach_on_equal=breach_on_equal
    )

    engine.process(
        TradeRealized(timestamp, -loss)
    )

    assert (
        engine.account.status
        is AccountStatus.TERMINATED
    ) == terminated


def test_commission_can_trigger_breach(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=-99_900,
            commission_cents=100
        )
    )

    assert not engine.account.is_active

    assert engine.account.realized_pnl_cents == -100_000


# ==================================================
# EOD trailing drawdown
# ==================================================

def test_eod_does_not_trail_intraday(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD
    )

    engine.process(
        TradeRealized(timestamp, 80_000)
    )

    assert engine.account.balance_cents == 2_580_000

    assert engine.account.high_water_mark_cents == 2_500_000

    assert engine.account.drawdown_floor_cents == 2_400_000


def test_eod_updates_at_session_end(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD
    )

    engine.process_all([
        TradeRealized(timestamp, 80_000),

        SessionEnded(
            timestamp,
            "day_1"
        )
    ])

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000


def test_eod_floor_enforced_intraday(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD
    )

    engine.process(
        EquityUpdated(timestamp, -100_000)
    )

    assert not engine.account.is_active


def test_eod_floor_never_decreases(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD
    )

    engine.process_all([
        TradeRealized(timestamp, 80_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        ),

        TradeRealized(
            timestamp + timedelta(days=1),
            -30_000
        ),

        SessionEnded(
            timestamp + timedelta(days=1),
            "day_2"
        )
    ])

    assert engine.account.balance_cents == 2_550_000

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000


def test_eod_uses_closing_value_not_intraday_peak(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD,
        reference=BalanceReference.EQUITY
    )

    engine.process_all([
        EquityUpdated(timestamp, 90_000),

        TradeRealized(timestamp, 40_000),

        SessionEnded(timestamp, "day_1")
    ])

    assert engine.account.high_water_mark_cents == 2_540_000

    assert engine.account.drawdown_floor_cents == 2_440_000


def test_eod_equity_reference(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_EOD,
        reference=BalanceReference.EQUITY
    )

    engine.process_all([
        EquityUpdated(timestamp, 50_000),

        SessionEnded(timestamp, "day_1")
    ])

    assert engine.account.balance_cents == 2_500_000

    assert engine.account.high_water_mark_cents == 2_550_000

    assert engine.account.drawdown_floor_cents == 2_450_000


# ==================================================
# Intraday trailing drawdown
# ==================================================

def test_intraday_balance_trailing(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY
    )

    engine.process(
        TradeRealized(timestamp, 80_000)
    )

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000


def test_intraday_equity_trailing(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.EQUITY
    )

    engine.process(
        EquityUpdated(timestamp, 80_000)
    )

    assert engine.account.balance_cents == 2_500_000

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000


def test_balance_reference_ignores_unrealized_high(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.BALANCE
    )

    engine.process(
        EquityUpdated(timestamp, 80_000)
    )

    assert engine.account.high_water_mark_cents == 2_500_000

    assert engine.account.drawdown_floor_cents == 2_400_000


def test_intraday_floor_never_decreases(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.EQUITY
    )

    engine.process_all([
        EquityUpdated(timestamp, 80_000),
        EquityUpdated(timestamp, 30_000)
    ])

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000

    assert engine.account.is_active


def test_intraday_unrealized_gain_can_cause_future_failure(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.EQUITY
    )

    engine.process_all([
        EquityUpdated(timestamp, 120_000),

        EquityUpdated(timestamp, 20_000)
    ])

    assert engine.account.equity_cents == 2_520_000

    assert engine.account.drawdown_floor_cents == 2_520_000

    assert not engine.account.is_active


def test_realization_does_not_double_count_equity(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.EQUITY
    )

    engine.process_all([
        EquityUpdated(timestamp, 80_000),

        TradeRealized(
            timestamp,
            gross_pnl_cents=80_000,
            commission_cents=500
        )
    ])

    assert engine.account.equity_cents == 2_579_500

    assert engine.account.high_water_mark_cents == 2_580_000

    assert engine.account.drawdown_floor_cents == 2_480_000


# ==================================================
# Trailing floor caps
# ==================================================

@pytest.mark.parametrize(
    "drawdown_type",
    [
        DrawdownType.TRAILING_EOD,
        DrawdownType.TRAILING_INTRADAY
    ]
)
def test_trailing_floor_cap(
    make_engine,
    timestamp,
    drawdown_type
):

    engine = make_engine(
        drawdown_type,
        cap=25_100
    )

    engine.process_all([
        TradeRealized(timestamp, 300_000),
        SessionEnded(timestamp, "day_1")
    ])

    assert engine.account.high_water_mark_cents == 2_800_000

    assert engine.account.drawdown_floor_cents == 2_510_000


def test_cap_does_not_stop_high_water_mark(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        cap=25_100
    )

    engine.process(
        TradeRealized(timestamp, 200_000)
    )

    engine.process(
        TradeRealized(timestamp, 100_000)
    )

    assert engine.account.high_water_mark_cents == 2_800_000

    assert engine.account.drawdown_floor_cents == 2_510_000


def test_cap_preserves_accumulated_profit_buffer(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        cap=25_100
    )

    engine.process_all([
        TradeRealized(timestamp, 300_000),
        TradeRealized(timestamp, -200_000)
    ])

    assert engine.account.balance_cents == 2_600_000

    assert engine.account.drawdown_floor_cents == 2_510_000

    assert engine.account.remaining_drawdown_cents == 90_000

    assert engine.account.is_active


def test_cap_equal_to_initial_floor(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        cap=24_000
    )

    engine.process(
        TradeRealized(timestamp, 200_000)
    )

    assert engine.account.drawdown_floor_cents == 2_400_000


# ==================================================
# Separate high-water and breach references
# ==================================================

def test_equity_breach_with_unchanged_balance(
    make_engine,
    timestamp
):

    engine = make_engine(
        breach_reference=BalanceReference.EQUITY
    )

    engine.process(
        EquityUpdated(timestamp, -100_000)
    )

    assert engine.account.balance_cents == 2_500_000

    assert not engine.account.is_active


def test_balance_breach_ignores_unrealized_loss(
    make_engine,
    timestamp
):

    engine = make_engine(
        breach_reference=BalanceReference.BALANCE
    )

    engine.process(
        EquityUpdated(timestamp, -150_000)
    )

    assert engine.account.is_active

    assert engine.account.remaining_drawdown_cents == 100_000


def test_mixed_reference_updated_floor_breach(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.BALANCE,
        breach_reference=BalanceReference.EQUITY
    )

    engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=100_000,
            remaining_unrealized_pnl_cents=-120_000
        )
    )

    assert engine.account.balance_cents == 2_600_000

    assert engine.account.equity_cents == 2_480_000

    assert engine.account.drawdown_floor_cents == 2_500_000

    assert not engine.account.is_active


def test_existing_floor_checked_before_update(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY,
        reference=BalanceReference.BALANCE,
        breach_reference=BalanceReference.EQUITY
    )

    engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=100_000,
            remaining_unrealized_pnl_cents=-220_000
        )
    )

    assert engine.account.equity_cents == 2_380_000

    assert engine.account.high_water_mark_cents == 2_500_000

    assert engine.account.drawdown_floor_cents == 2_400_000

    assert not engine.account.is_active


# ==================================================
# Termination behavior
# ==================================================

def test_breach_record(make_engine, timestamp):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -125_000)
    )

    breach = engine.last_drawdown_breach

    assert breach is not None

    assert breach.timestamp == timestamp

    assert breach.monitored_value_cents == 2_375_000

    assert breach.floor_cents == 2_400_000

    assert breach.high_water_mark_cents == 2_500_000

    assert breach.overshoot_cents == 25_000


def test_equality_breach_has_zero_overshoot(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    assert engine.last_drawdown_breach.overshoot_cents == 0


def test_breaching_trade_remains_accounted_for(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(
            timestamp,
            -120_000,
            500
        )
    )

    account = engine.account

    assert account.realized_pnl_cents == -120_500

    assert account.commissions_cents == 500

    assert account.trade_count == 1

    assert account.session_trade_count == 1

    assert engine.processed_event_count == 2

    assert account.last_event_timestamp == timestamp


def test_terminated_account_cannot_recover(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    before = deepcopy(engine.account)

    with pytest.raises(InvalidAccountStateError):

        engine.process(
            TradeRealized(timestamp, 200_000)
        )

    assert engine.account == before


def test_batch_stops_at_breach(
    make_engine,
    timestamp
):

    engine = make_engine()

    processed = engine.process_all([
        TradeRealized(timestamp, 20_000),

        TradeRealized(timestamp, -150_000),

        TradeRealized(timestamp, 300_000)
    ])

    assert processed == 2

    assert engine.account.trade_count == 2

    assert engine.account.realized_pnl_cents == -130_000

    assert not engine.account.is_active


def test_terminated_batch_processes_nothing(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    assert engine.process_all([
        TradeRealized(timestamp, 200_000)
    ]) == 0


def test_batch_does_not_consume_after_termination(
    make_engine,
    timestamp
):

    engine = make_engine()

    def events():

        yield TradeRealized(
            timestamp,
            -100_000
        )

        pytest.fail(
            "Generator consumed after account termination"
        )

    assert engine.process_all(events()) == 1


def test_rejected_event_does_not_modify_drawdown(
    make_engine,
    timestamp
):

    engine = make_engine(
        DrawdownType.TRAILING_INTRADAY
    )

    before = deepcopy(engine.account)

    with pytest.raises(EventProcessingError):

        engine.process(
            SessionEnded(
                timestamp,
                "incorrect_session"
            )
        )

    assert engine.account == before


# ==================================================
# Account metrics
# ==================================================

def test_remaining_drawdown(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        EquityUpdated(timestamp, -30_000)
    )

    assert engine.account.remaining_drawdown_cents == 70_000

    assert engine.account.current_drawdown_cents == 30_000


def test_negative_remaining_drawdown_on_breach(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -120_000)
    )

    assert engine.account.remaining_drawdown_cents == -20_000


# ==================================================
# Drawdown invariants
# ==================================================

def test_initial_drawdown_invariants(make_engine):

    engine = make_engine()

    assert_drawdown_invariants(
        engine.account,
        engine.drawdown
    )


def test_invalid_floor_detected(make_engine):

    engine = make_engine()

    engine.account.drawdown_floor_cents += 100

    with pytest.raises(DrawdownInvariantError):

        assert_drawdown_invariants(
            engine.account,
            engine.drawdown
        )


def test_invalid_high_water_mark_detected(make_engine):

    engine = make_engine()

    engine.account.high_water_mark_cents = 2_490_000

    with pytest.raises(DrawdownInvariantError):

        assert_drawdown_invariants(
            engine.account,
            engine.drawdown
        )


def test_static_high_water_mark_cannot_change(
    make_engine
):

    engine = make_engine()

    engine.account.high_water_mark_cents += 100

    engine.account.drawdown_floor_cents += 100

    with pytest.raises(DrawdownInvariantError):

        assert_drawdown_invariants(
            engine.account,
            engine.drawdown
        )


def test_strict_mode_detects_corruption(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.account.drawdown_floor_cents -= 500

    before = deepcopy(engine.account)

    with pytest.raises(DrawdownInvariantError):

        engine.process(
            TradeRealized(timestamp, 1000)
        )

    assert engine.account == before


# ==================================================
# Engine reconstruction
# ==================================================

def test_reconstructed_engine_preserves_floor(
    make_engine,
    timestamp
):

    original = make_engine(
        DrawdownType.TRAILING_INTRADAY
    )

    original.process(
        TradeRealized(timestamp, 80_000)
    )

    account = original.account

    expected_floor = account.drawdown_floor_cents

    reopened = AccountEngine(account)

    assert reopened.account.drawdown_floor_cents == expected_floor

    assert reopened.account.high_water_mark_cents == 2_580_000


def test_existing_account_missing_drawdown_state(
    make_engine
):

    engine = make_engine()

    engine.account.high_water_mark_cents = None

    engine.account.drawdown_floor_cents = None

    with pytest.raises(DrawdownInvariantError):

        AccountEngine(engine.account)


# ==================================================
# Strict mode and determinism
# ==================================================

@pytest.mark.parametrize(
    "strict",
    [True, False]
)
def test_breach_enforced_in_all_modes(
    make_engine,
    timestamp,
    strict
):

    engine = make_engine(strict=strict)

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    assert not engine.account.is_active


@pytest.mark.parametrize(
    "drawdown_type",
    list(DrawdownType)
)
def test_strict_fast_equivalence(
    make_engine,
    timestamp,
    drawdown_type
):

    strict = make_engine(
        drawdown_type,
        strict=True,
        reference=BalanceReference.EQUITY
    )

    fast = make_engine(
        drawdown_type,
        strict=False,
        reference=BalanceReference.EQUITY
    )

    events = [
        EquityUpdated(timestamp, 80_000),

        TradeRealized(timestamp, 50_000),

        EquityUpdated(timestamp, 100_000),

        TradeRealized(timestamp, -120_000),

        SessionEnded(timestamp, "day_1")
    ]

    strict.process_all(events)

    fast.process_all(events)

    assert strict.account == fast.account

    assert (
        strict.last_drawdown_breach
        == fast.last_drawdown_breach
    )

    assert (
        strict.processed_event_count
        == fast.processed_event_count
    )