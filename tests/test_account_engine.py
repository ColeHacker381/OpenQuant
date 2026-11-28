import pytest

from copy import deepcopy
from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import (
    CashFlow,
    create_account
)

from openquant.simulation.enums import (
    AccountStatus,
    CashFlowType
)

from openquant.simulation.events import (
    AccountEvent,
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized,
    PayoutRequested,
    StageActivationRequested
)

from openquant.simulation.accounting import (
    AccountingError,
    AccountingInvariantError,
    assert_accounting_invariants,
    record_cash_flow
)

from openquant.simulation.engine import (
    AccountEngine,
    EventProcessingError,
    EventOrderError,
    InvalidAccountStateError,
    UnsupportedEventError
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
def revision():

    firm = PropFirm(
        name="Example Trading",

        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,

                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000
                ),

                fees=FeeRules(purchase=100)
            )
        )
    )

    return FirmRevision(
        firm=firm,
        metadata=RevisionMetadata()
    )


@pytest.fixture
def account(revision):

    return create_account(
        "account_001",
        revision,
        SimulationMode.EVALUATION_ONLY,
        require_verified=False
    )


@pytest.fixture
def engine(account):

    return AccountEngine(
        account,
        strict=True
    )


@pytest.fixture
def active_engine(engine, timestamp):

    engine.process(
        SessionStarted(
            timestamp,
            "session_001"
        )
    )

    return engine


def assert_rejected(
    engine,
    event,
    expected_exception
):

    before = deepcopy(engine.account)

    count = engine.processed_event_count

    with pytest.raises(expected_exception):

        engine.process(event)

    assert engine.account == before

    assert engine.processed_event_count == count


# ==================================================
# Engine initialization
# ==================================================

def test_engine_initialization(engine, account):

    assert engine.account is account

    assert engine.strict is True

    assert engine.processed_event_count == 0


def test_engine_requires_account():

    with pytest.raises(TypeError):

        AccountEngine({})


def test_engine_requires_boolean_strict(account):

    with pytest.raises(TypeError):

        AccountEngine(
            account,
            strict="false"
        )


def test_engine_rejects_corrupted_initial_state(account):

    account.balance_cents += 100

    with pytest.raises(AccountingInvariantError):

        AccountEngine(account)


# ==================================================
# Accounting invariants
# ==================================================

def test_initial_invariants(account):

    assert_accounting_invariants(account)


def test_balance_invariant_violation(account):

    account.balance_cents += 1000

    with pytest.raises(
        AccountingInvariantError,
        match="Balance mismatch"
    ):

        assert_accounting_invariants(account)


def test_float_balance_rejected(account):

    account.balance_cents = 2_500_000.0

    with pytest.raises(AccountingInvariantError):

        assert_accounting_invariants(account)


def test_negative_commissions_rejected(account):

    account.commissions_cents = -100

    with pytest.raises(AccountingInvariantError):

        assert_accounting_invariants(account)


def test_stale_session_state_rejected(account):

    account.session_trade_count = 1

    account.trade_count = 1

    with pytest.raises(AccountingInvariantError):

        assert_accounting_invariants(account)


def test_session_trade_count_invariant(
    active_engine
):

    active_engine.account.session_trade_count = 1

    with pytest.raises(AccountingInvariantError):

        assert_accounting_invariants(
            active_engine.account
        )


def test_stage_withdrawals_exceed_lifetime(account):

    account.gross_withdrawals_cents = 100

    account.balance_cents -= 100

    with pytest.raises(AccountingInvariantError):

        assert_accounting_invariants(account)


# ==================================================
# Event ordering
# ==================================================

def test_first_event_timestamp(engine, timestamp):

    engine.process(
        SessionStarted(
            timestamp,
            "session_001"
        )
    )

    assert (
        engine.account.last_event_timestamp
        == timestamp
    )

    assert engine.processed_event_count == 1


def test_out_of_order_event(
    active_engine,
    timestamp
):

    event = EquityUpdated(
        timestamp - timedelta(seconds=1),
        1000
    )

    assert_rejected(
        active_engine,
        event,
        EventOrderError
    )


def test_equal_timestamp_allowed(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(
            timestamp,
            1000
        )
    )

    assert active_engine.processed_event_count == 2

    assert (
        active_engine.account.unrealized_pnl_cents
        == 1000
    )


def test_later_timestamp_accepted(
    active_engine,
    timestamp
):

    later = timestamp + timedelta(minutes=1)

    active_engine.process(
        EquityUpdated(later, 1000)
    )

    assert (
        active_engine.account.last_event_timestamp
        == later
    )


def test_invalid_event_type(engine):

    assert_rejected(
        engine,
        {},
        TypeError
    )


# ==================================================
# Unsupported events
# ==================================================

@pytest.mark.parametrize(
    "event_cls",
    [
        AccountEvent,
        StageActivationRequested
    ]
)
def test_unsupported_events(
    engine,
    timestamp,
    event_cls
):

    assert_rejected(
        engine,
        event_cls(timestamp),
        UnsupportedEventError
    )


def test_payout_request_not_implemented(
    engine,
    timestamp
):

    event = PayoutRequested(
        timestamp,
        25_000
    )

    assert_rejected(
        engine,
        event,
        UnsupportedEventError
    )


# ==================================================
# Account status restrictions
# ==================================================

@pytest.mark.parametrize(
    "status",
    [
        AccountStatus.TERMINATED,
        AccountStatus.CLOSED,
        AccountStatus.AWAITING_ACTIVATION
    ]
)
def test_inactive_account_rejects_events(
    engine,
    timestamp,
    status
):

    engine.account.status = status

    assert_rejected(
        engine,

        SessionStarted(
            timestamp,
            "session_001"
        ),

        InvalidAccountStateError
    )


# ==================================================
# Session initialization
# ==================================================

def test_start_session(
    active_engine
):

    account = active_engine.account

    assert account.has_active_session

    assert account.session_id == "session_001"

    assert (
        account.session_start_balance_cents
        == 2_500_000
    )

    assert (
        account.session_start_equity_cents
        == 2_500_000
    )

    assert account.session_trade_count == 0

    assert account.session_realized_pnl_cents == 0


def test_start_session_with_unrealized_pnl(
    engine,
    timestamp
):

    engine.account.unrealized_pnl_cents = -10_000

    engine.process(
        SessionStarted(
            timestamp,
            "session_001"
        )
    )

    assert (
        engine.account.session_start_balance_cents
        == 2_500_000
    )

    assert (
        engine.account.session_start_equity_cents
        == 2_490_000
    )


def test_cannot_start_overlapping_sessions(
    active_engine,
    timestamp
):

    assert_rejected(
        active_engine,

        SessionStarted(
            timestamp + timedelta(minutes=1),
            "session_002"
        ),

        EventProcessingError
    )


def test_cannot_reopen_completed_session(
    active_engine,
    timestamp
):

    active_engine.process(
        SessionEnded(
            timestamp + timedelta(hours=1),
            "session_001"
        )
    )

    assert_rejected(
        active_engine,

        SessionStarted(
            timestamp + timedelta(days=1),
            "session_001"
        ),

        EventProcessingError
    )


# ==================================================
# Unrealized PnL
# ==================================================

def test_positive_equity_update(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(
            timestamp,
            40_000
        )
    )

    account = active_engine.account

    assert account.balance_cents == 2_500_000

    assert account.equity_cents == 2_540_000

    assert account.unrealized_pnl_cents == 40_000

    assert account.realized_pnl_cents == 0


def test_negative_equity_update(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(
            timestamp,
            -40_000
        )
    )

    assert (
        active_engine.account.equity_cents
        == 2_460_000
    )


def test_equity_updates_replace_pnl(
    active_engine,
    timestamp
):

    active_engine.process_all([
        EquityUpdated(timestamp, 10_000),

        EquityUpdated(
            timestamp + timedelta(seconds=1),
            15_000
        )
    ])

    assert (
        active_engine.account.unrealized_pnl_cents
        == 15_000
    )

    assert (
        active_engine.account.equity_cents
        == 2_515_000
    )


def test_equity_requires_active_session(
    engine,
    timestamp
):

    assert_rejected(
        engine,
        EquityUpdated(timestamp, 1000),
        EventProcessingError
    )


# ==================================================
# Realized trade accounting
# ==================================================

def test_winning_trade(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=40_000,
            commission_cents=500
        )
    )

    account = active_engine.account

    assert account.balance_cents == 2_539_500

    assert account.realized_pnl_cents == 39_500

    assert account.commissions_cents == 500

    assert account.trade_count == 1

    assert account.session_trade_count == 1

    assert account.session_realized_pnl_cents == 39_500


def test_losing_trade(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=-25_000,
            commission_cents=500
        )
    )

    account = active_engine.account

    assert account.balance_cents == 2_474_500

    assert account.realized_pnl_cents == -25_500

    assert account.commissions_cents == 500


def test_break_even_trade_with_commission(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=0,
            commission_cents=250
        )
    )

    assert (
        active_engine.account.realized_pnl_cents
        == -250
    )

    assert active_engine.account.trade_count == 1


def test_zero_commission(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=1000
        )
    )

    assert active_engine.account.commissions_cents == 0

    assert active_engine.account.realized_pnl_cents == 1000


def test_multiple_trades(
    active_engine,
    timestamp
):

    active_engine.process_all([
        TradeRealized(
            timestamp,
            40_000,
            500
        ),

        TradeRealized(
            timestamp,
            -25_000,
            500
        ),

        TradeRealized(
            timestamp,
            50_000,
            500
        )
    ])

    account = active_engine.account

    assert account.trade_count == 3

    assert account.realized_pnl_cents == 63_500

    assert account.gross_realized_pnl_cents == 65_000

    assert account.commissions_cents == 1500

    assert account.balance_cents == 2_563_500


def test_trade_requires_active_session(
    engine,
    timestamp
):

    assert_rejected(
        engine,
        TradeRealized(timestamp, 10_000),
        EventProcessingError
    )


def test_realization_clears_unrealized_pnl(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(timestamp, 40_000)
    )

    active_engine.process(
        TradeRealized(
            timestamp,
            40_000,
            500
        )
    )

    account = active_engine.account

    assert account.unrealized_pnl_cents == 0

    assert account.equity_cents == 2_539_500


def test_partial_realization(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(timestamp, 40_000)
    )

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=25_000,
            commission_cents=250,
            remaining_unrealized_pnl_cents=15_000
        )
    )

    account = active_engine.account

    assert account.balance_cents == 2_524_750

    assert account.unrealized_pnl_cents == 15_000

    assert account.equity_cents == 2_539_750


def test_locked_session_accepts_existing_fill(
    active_engine,
    timestamp
):

    active_engine.account.session_locked = True

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=-1000
        )
    )

    assert (
        active_engine.account.realized_pnl_cents
        == -1000
    )

    assert active_engine.account.session_locked


def test_loss_recorded_before_drawdown_termination(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=-150_000
        )
    )

    account = active_engine.account

    # The financial loss must remain recorded.
    assert account.balance_cents == 2_350_000

    assert account.realized_pnl_cents == -150_000

    assert not account.is_active

    assert account.status is AccountStatus.TERMINATED

    assert active_engine.processed_event_count == 2

# ==================================================
# Session completion
# ==================================================

def test_end_session(
    active_engine,
    timestamp
):

    active_engine.process(
        SessionEnded(
            timestamp + timedelta(hours=1),
            "session_001"
        )
    )

    account = active_engine.account

    assert not account.has_active_session

    assert account.completed_sessions == 1

    assert account.session_id is None


def test_session_record_contains_pnl(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(
            timestamp,
            40_000,
            500
        )
    )

    active_engine.process(
        SessionEnded(
            timestamp,
            "session_001"
        )
    )

    record = active_engine.account.session_history[0]

    assert record.starting_balance_cents == 2_500_000

    assert record.ending_balance_cents == 2_539_500

    assert record.net_trading_pnl_cents == 39_500

    assert record.trade_count == 1

    assert record.was_profitable


def test_session_state_cleared(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(timestamp, 1000)
    )

    active_engine.account.session_locked = True

    active_engine.process(
        SessionEnded(timestamp, "session_001")
    )

    account = active_engine.account

    assert account.session_start_balance_cents is None

    assert account.session_start_equity_cents is None

    assert account.session_realized_pnl_cents == 0

    assert account.session_trade_count == 0

    assert not account.session_locked

    assert account.trade_count == 1

    assert account.realized_pnl_cents == 1000


def test_cannot_end_inactive_session(
    engine,
    timestamp
):

    assert_rejected(
        engine,
        SessionEnded(timestamp, "session_001"),
        EventProcessingError
    )


def test_mismatched_session_id(
    active_engine,
    timestamp
):

    assert_rejected(
        active_engine,

        SessionEnded(
            timestamp,
            "incorrect_session"
        ),

        EventProcessingError
    )


def test_unrealized_pnl_preserved_at_session_end(
    active_engine,
    timestamp
):

    active_engine.process(
        EquityUpdated(timestamp, 1000)
    )

    active_engine.process(
        SessionEnded(timestamp, "session_001")
    )

    assert (
        active_engine.account.unrealized_pnl_cents
        == 1000
    )


# ==================================================
# External cash accounting
# ==================================================

def test_record_purchase_fee(account, timestamp):

    record_cash_flow(
        account,

        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            -10_000
        )
    )

    assert account.net_cash_flow_cents == -10_000

    assert account.balance_cents == 2_500_000

    assert account.realized_pnl_cents == 0


def test_multiple_external_fees(account, timestamp):

    flows = [
        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            -10_000
        ),

        CashFlow(
            timestamp,
            CashFlowType.ACTIVATION,
            -5000
        ),

        CashFlow(
            timestamp,
            CashFlowType.RECURRING_FEE,
            -2500
        )
    ]

    for flow in flows:

        record_cash_flow(account, flow)

    assert account.net_cash_flow_cents == -17_500

    assert account.total_cash_expenses_cents == 17_500

    assert account.balance_cents == 2_500_000


def test_record_refund(account, timestamp):

    record_cash_flow(
        account,

        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            -10_000
        )
    )

    record_cash_flow(
        account,

        CashFlow(
            timestamp,
            CashFlowType.REFUND,
            10_000
        )
    )

    assert account.net_cash_flow_cents == 0


def test_direct_payout_recording_rejected(
    account,
    timestamp
):

    before = deepcopy(account)

    with pytest.raises(AccountingError):

        record_cash_flow(
            account,

            CashFlow(
                timestamp,
                CashFlowType.PAYOUT,
                90_000
            )
        )

    assert account == before


def test_cash_flow_requires_correct_type(account):

    with pytest.raises(TypeError):

        record_cash_flow(account, {})


# ==================================================
# Performance metrics
# ==================================================

def test_empty_performance_metrics(account):

    assert account.completed_sessions == 0

    assert account.trading_days == 0

    assert account.profitable_days == 0

    assert account.best_day_pnl_cents == 0

    assert account.completed_session_pnl_cents == 0


def test_current_session_not_counted(
    active_engine,
    timestamp
):

    active_engine.process(
        TradeRealized(timestamp, 1000)
    )

    account = active_engine.account

    assert account.trading_days == 0

    assert account.profitable_days == 0

    assert account.completed_session_pnl_cents == 0


def test_multiple_session_metrics(
    engine,
    timestamp
):

    for index, pnl in enumerate(
        [39_500, -25_500, 49_500, None, 15_000]
    ):

        current = timestamp + timedelta(days=index)

        session_id = f"session_{index}"

        engine.process(
            SessionStarted(
                current,
                session_id
            )
        )

        if pnl is not None:

            engine.process(
                TradeRealized(
                    current,
                    pnl
                )
            )

        engine.process(
            SessionEnded(
                current + timedelta(hours=1),
                session_id
            )
        )

    account = engine.account

    assert account.completed_sessions == 5

    assert account.trading_days == 4

    assert account.profitable_days == 3

    assert account.best_day_pnl_cents == 49_500

    assert account.realized_pnl_cents == 78_500

    assert account.completed_session_pnl_cents == 78_500


def test_losing_days_have_no_positive_best_day(
    active_engine,
    timestamp
):

    active_engine.process_all([
        TradeRealized(timestamp, -1000),

        SessionEnded(
            timestamp,
            "session_001"
        )
    ])

    assert active_engine.account.best_day_pnl_cents == 0

    assert active_engine.account.profitable_days == 0


# ==================================================
# Batch event processing
# ==================================================

def test_process_all(engine, timestamp):

    events = [
        SessionStarted(
            timestamp,
            "session_001"
        ),

        TradeRealized(
            timestamp,
            10_000,
            250
        ),

        SessionEnded(
            timestamp,
            "session_001"
        )
    ]

    processed = engine.process_all(events)

    assert processed == 3

    assert engine.processed_event_count == 3

    assert engine.account.realized_pnl_cents == 9750


def test_process_empty_batch(engine):

    assert engine.process_all([]) == 0

    assert engine.processed_event_count == 0


def test_process_generator(
    active_engine,
    timestamp
):

    events = (
        TradeRealized(
            timestamp + timedelta(seconds=i),
            100
        )

        for i in range(10)
    )

    assert active_engine.process_all(events) == 10

    assert active_engine.account.trade_count == 10

    assert active_engine.account.realized_pnl_cents == 1000


def test_batch_stops_on_error(engine, timestamp):

    events = [
        SessionStarted(
            timestamp,
            "session_001"
        ),

        TradeRealized(
            timestamp,
            1000
        ),

        SessionEnded(
            timestamp,
            "incorrect_session"
        ),

        TradeRealized(
            timestamp,
            2000
        )
    ]

    with pytest.raises(EventProcessingError):

        engine.process_all(events)

    assert engine.processed_event_count == 2

    assert engine.account.realized_pnl_cents == 1000

    assert engine.account.has_active_session


# ==================================================
# Determinism and strict mode
# ==================================================

def test_strict_and_fast_modes_equivalent(
    account,
    timestamp
):

    strict_engine = AccountEngine(
        deepcopy(account),
        strict=True
    )

    fast_engine = AccountEngine(
        deepcopy(account),
        strict=False
    )

    events = [
        SessionStarted(
            timestamp,
            "session_001"
        ),

        EquityUpdated(timestamp, 40_000),

        TradeRealized(
            timestamp,
            40_000,
            500
        ),

        TradeRealized(
            timestamp,
            -25_000,
            500
        ),

        SessionEnded(
            timestamp,
            "session_001"
        )
    ]

    strict_engine.process_all(events)

    fast_engine.process_all(events)

    assert strict_engine.account == fast_engine.account

    assert (
        strict_engine.processed_event_count
        == fast_engine.processed_event_count
    )


def test_fast_mode_still_validates_events(
    account,
    timestamp
):

    engine = AccountEngine(
        account,
        strict=False
    )

    assert_rejected(
        engine,

        TradeRealized(timestamp, 1000),

        EventProcessingError
    )


def test_strict_mode_detects_corruption(
    active_engine,
    timestamp
):

    active_engine.account.balance_cents += 100

    before = deepcopy(active_engine.account)

    with pytest.raises(AccountingInvariantError):

        active_engine.process(
            EquityUpdated(timestamp, 1000)
        )

    assert active_engine.account == before


def test_repeated_simulation_is_deterministic(
    account,
    timestamp
):

    events = [
        SessionStarted(
            timestamp,
            "session_001"
        ),

        TradeRealized(timestamp, 1000, 100),

        TradeRealized(timestamp, -500, 100),

        SessionEnded(
            timestamp,
            "session_001"
        )
    ]

    results = []

    for _ in range(3):

        engine = AccountEngine(
            deepcopy(account)
        )

        engine.process_all(events)

        results.append(engine.account)

    assert results[0] == results[1] == results[2]


def test_independent_engines(
    account,
    timestamp
):

    first = AccountEngine(
        deepcopy(account)
    )

    second = AccountEngine(
        deepcopy(account)
    )

    first.process_all([
        SessionStarted(
            timestamp,
            "session_001"
        ),

        TradeRealized(timestamp, 1000)
    ])

    assert first.account.realized_pnl_cents == 1000

    assert second.account.realized_pnl_cents == 0

    assert account.realized_pnl_cents == 0