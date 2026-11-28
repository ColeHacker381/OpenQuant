import pytest

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from fractions import Fraction

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    FundedRules,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    PayoutRules,
    ConsistencyRules,
    ConsistencyWindow,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import (
    create_account,
    CashFlow
)

from openquant.simulation.accounting import (
    AccountingInvariantError,
    audit_payout_accounting,
    record_cash_flow
)

from openquant.simulation.enums import CashFlowType

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    EquityUpdated,
    PayoutRequested
)

from openquant.simulation.engine import (
    AccountEngine,
    EventProcessingError,
    EventOrderError,
    UnsupportedEventError
)

from openquant.simulation.payout import (
    PayoutConfigurationError,
    PayoutRejectionReason,
    compile_payout,
    calculate_maximum_withdrawal,
    calculate_trader_payout,
    get_payout_cycle_stats,
    check_payout_consistency
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
def make_engine():

    def factory(
        *,
        payout=None,
        stage_consistency=None,
        strict=True,
        drawdown_type=DrawdownType.STATIC,
        breach_on_equal=True,
        trailing_cap=None,
        mode=SimulationMode.FUNDED_ONLY
    ):

        if payout is None:

            payout = PayoutRules(
                profit_split=0.90,
                minimum_amount=250
            )

        drawdown = DrawdownRules(
            type=drawdown_type,
            amount=1000,
            breach_on_equal=breach_on_equal,
            trailing_stop_balance=trailing_cap
        )

        firm = PropFirm(
            "Example Trading",

            Ruleset(
                account_name="25K",

                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,
                    drawdown=drawdown,
                    fees=FeeRules(purchase=100)
                ),

                funded=FundedRules(
                    drawdown=drawdown,

                    fees=FeeRules(activation=50),

                    payout=payout,

                    consistency=stage_consistency
                )
            )
        )

        revision = FirmRevision(
            firm=firm,
            metadata=RevisionMetadata()
        )

        account = create_account(
            "account_001",
            revision,
            mode,
            require_verified=False
        )

        return AccountEngine(
            account,
            strict=strict
        )

    return factory


def process_days(engine, timestamp, profits):

    for index, pnl in enumerate(profits):

        t = timestamp + timedelta(days=index)

        session_id = t.date().isoformat()

        events = [
            SessionStarted(t, session_id)
        ]

        if pnl is not None:

            events.append(
                TradeRealized(t, pnl)
            )

        events.append(
            SessionEnded(
                t + timedelta(hours=1),
                session_id,
                positions_flat=True
            )
        )

        engine.process_all(events)


def request(timestamp, amount=25_000, flat=True):

    return PayoutRequested(
        timestamp,
        amount,
        positions_flat=flat
    )


def assert_payout_rejected(engine, event, reason):

    before = deepcopy(engine.account)

    count = engine.processed_event_count

    previous_payout = engine.last_payout

    decision = engine.assess_payout(event)

    assert not decision.allowed

    assert reason in decision.reasons

    with pytest.raises(EventProcessingError):

        engine.process(event)

    assert engine.account == before

    assert engine.processed_event_count == count

    assert engine.last_payout == previous_payout


# ==================================================
# Compilation and exact monetary arithmetic
# ==================================================

def test_compile_payout():

    compiled = compile_payout(
        PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=1000,
            maximum_withdrawal_ratio=0.50
        )
    )

    assert compiled.profit_split == Fraction(9, 10)

    assert compiled.minimum_amount_cents == 25_000

    assert compiled.maximum_amount_cents == 100_000

    assert (
        compiled.maximum_withdrawal_ratio
        == Fraction(1, 2)
    )


@pytest.mark.parametrize(
    "split",
    [-0.1, 1.1, True, float("nan"), float("inf")]
)
def test_invalid_profit_split(split):

    with pytest.raises(PayoutConfigurationError):

        compile_payout(
            PayoutRules(
                profit_split=split,
                minimum_amount=250
            )
        )


def test_fractional_cent_minimum_rejected():

    with pytest.raises(PayoutConfigurationError):

        compile_payout(
            PayoutRules(
                profit_split=0.90,
                minimum_amount=250.001
            )
        )


def test_maximum_below_minimum_rejected():

    with pytest.raises(PayoutConfigurationError):

        compile_payout(
            PayoutRules(
                profit_split=0.90,
                minimum_amount=250,
                maximum_amount=100
            )
        )


@pytest.mark.parametrize(
    "gross,split,expected",
    [
        (100_000, Fraction(9, 10), 90_000),
        (25_001, Fraction(9, 10), 22_500),
        (100_000, Fraction(1), 100_000),
        (100_000, Fraction(0), 0)
    ]
)
def test_trader_payout(gross, split, expected):

    assert calculate_trader_payout(
        gross,
        split
    ) == expected


# ==================================================
# Payout initialization and stage restrictions
# ==================================================

def test_initial_payout_state(make_engine):

    engine = make_engine()

    account = engine.account

    assert engine.payout is not None

    assert account.payout_count == 0

    assert account.payout_history == []

    assert account.payout_cycle_start_index == 0

    assert account.last_payout_timestamp is None


def test_evaluation_cannot_request_payout(
    make_engine,
    timestamp
):

    engine = make_engine(
        mode=SimulationMode.EVALUATION_ONLY
    )

    assert engine.payout is None

    decision = engine.assess_payout(
        request(timestamp)
    )

    assert not decision.allowed

    assert (
        PayoutRejectionReason.INVALID_STAGE
        in decision.reasons
    )

    with pytest.raises(UnsupportedEventError):

        engine.process(request(timestamp))


def test_active_session_rejects_payout(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        SessionStarted(timestamp, "day_1")
    )

    engine.process(
        TradeRealized(timestamp, 100_000)
    )

    assert_payout_rejected(
        engine,

        request(timestamp),

        PayoutRejectionReason.ACTIVE_SESSION
    )


@pytest.mark.parametrize("flat", [None, False])
def test_flat_confirmation_required(
    make_engine,
    timestamp,
    flat
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    assert_payout_rejected(
        engine,

        request(
            timestamp + timedelta(days=1),
            flat=flat
        ),

        PayoutRejectionReason.POSITIONS_NOT_FLAT
    )


def test_unrealized_profit_prevents_payout(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(
            timestamp,
            100_000,
            remaining_unrealized_pnl_cents=100
        ),

        SessionEnded(timestamp, "day_1")
    ])

    assert_payout_rejected(
        engine,

        request(timestamp),

        PayoutRejectionReason.POSITIONS_NOT_FLAT
    )


def test_invalid_flat_confirmation(timestamp):

    with pytest.raises(TypeError):

        request(timestamp, flat="yes")


# ==================================================
# Monetary payout capacity
# ==================================================

def test_no_profit_no_withdrawal(make_engine):

    engine = make_engine()

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 0


def test_available_profit_limit(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 100_000


def test_individual_payout_cap(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=750
        )
    )

    process_days(engine, timestamp, [200_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 75_000


def test_withdrawal_ratio(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_withdrawal_ratio=0.50
        )
    )

    process_days(engine, timestamp, [200_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 100_000


def test_minimum_remaining_balance(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,

            minimum_balance_after_payout=26_500
        )
    )

    process_days(engine, timestamp, [200_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 50_000


def test_most_restrictive_limit(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=1500,
            maximum_withdrawal_ratio=0.50,
            minimum_balance_after_payout=26_250
        )
    )

    process_days(engine, timestamp, [200_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 75_000


@pytest.mark.parametrize(
    "breach_on_equal,expected",
    [
        (True, 99_999),
        (False, 100_000)
    ]
)
def test_drawdown_limits_withdrawal(
    make_engine,
    timestamp,
    breach_on_equal,
    expected
):

    engine = make_engine(
        drawdown_type=DrawdownType.TRAILING_EOD,
        breach_on_equal=breach_on_equal
    )

    process_days(engine, timestamp, [200_000])

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == expected


def test_drawdown_boundary_withdrawal_rejected(
    make_engine,
    timestamp
):

    engine = make_engine(
        drawdown_type=DrawdownType.TRAILING_EOD
    )

    process_days(engine, timestamp, [200_000])

    assert_payout_rejected(
        engine,

        request(
            timestamp + timedelta(days=1),
            100_000
        ),

        PayoutRejectionReason.ABOVE_MAXIMUM
    )


def test_one_cent_above_drawdown_survives(
    make_engine,
    timestamp
):

    engine = make_engine(
        drawdown_type=DrawdownType.TRAILING_EOD
    )

    process_days(engine, timestamp, [200_000])

    engine.process(
        request(
            timestamp + timedelta(days=1),
            99_999
        )
    )

    assert engine.account.is_active

    assert (
        engine.account.remaining_drawdown_cents
        == 1
    )


# ==================================================
# Minimum payout and rejection behavior
# ==================================================

def test_below_minimum_rejected(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    assert_payout_rejected(
        engine,

        request(
            timestamp + timedelta(days=1),
            24_999
        ),

        PayoutRejectionReason.BELOW_MINIMUM
    )


def test_exact_minimum_accepted(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    engine.process(
        request(timestamp + timedelta(days=1))
    )

    assert engine.account.payout_count == 1


def test_excessive_withdrawal_rejected(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    assert_payout_rejected(
        engine,

        request(
            timestamp + timedelta(days=1),
            100_001
        ),

        PayoutRejectionReason.ABOVE_MAXIMUM
    )


def test_out_of_order_payout(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    before = deepcopy(engine.account)

    with pytest.raises(EventOrderError):

        engine.process(
            request(timestamp)
        )

    assert engine.account == before


# ==================================================
# Trading and profitable days
# ==================================================

def test_qualifying_day_statistics(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            profitable_day_threshold=100
        )
    )

    process_days(
        engine,
        timestamp,
        [25_000, -10_000, 5000, 10_000, None]
    )

    stats = get_payout_cycle_stats(
        engine.account,
        engine.payout
    )

    assert stats.trading_days == 4

    assert stats.profitable_days == 2

    assert stats.total_profit_cents == 30_000

    assert stats.best_day_cents == 25_000


def test_breakeven_day_not_profitable(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [0])

    stats = get_payout_cycle_stats(
        engine.account,
        engine.payout
    )

    assert stats.trading_days == 1

    assert stats.profitable_days == 0


def test_minimum_trading_days(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            minimum_trading_days=3
        )
    )

    process_days(engine, timestamp, [100_000])

    assert_payout_rejected(
        engine,

        request(timestamp + timedelta(days=1)),

        PayoutRejectionReason.MINIMUM_TRADING_DAYS
    )


def test_minimum_profitable_days(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            minimum_profitable_days=3,

            profitable_day_threshold=100
        )
    )

    process_days(
        engine,
        timestamp,
        [50_000, 30_000, 5000]
    )

    assert_payout_rejected(
        engine,

        request(timestamp + timedelta(days=3)),

        PayoutRejectionReason.MINIMUM_PROFITABLE_DAYS
    )


# ==================================================
# Consistency
# ==================================================

def test_payout_consistency_blocks_withdrawal(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            consistency=ConsistencyRules(
                maximum_ratio=0.40
            )
        )
    )

    process_days(
        engine,
        timestamp,
        [70_000, 30_000, 25_000]
    )

    assert_payout_rejected(
        engine,

        request(timestamp + timedelta(days=3)),

        PayoutRejectionReason.CONSISTENCY
    )


def test_exact_consistency_boundary(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            consistency=ConsistencyRules(
                maximum_ratio=0.40
            )
        )
    )

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 25_000]
    )

    assert engine.assess_payout(
        request(timestamp + timedelta(days=3))
    ).allowed


def test_stage_consistency_applied(
    make_engine,
    timestamp
):

    engine = make_engine(
        stage_consistency=ConsistencyRules(0.40)
    )

    process_days(engine, timestamp, [100_000])

    assert_payout_rejected(
        engine,

        request(timestamp + timedelta(days=1)),

        PayoutRejectionReason.CONSISTENCY
    )


def test_consistency_window_difference(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 50_000]
    )

    engine.process(
        request(
            timestamp + timedelta(days=3)
        )
    )

    process_days(
        engine,
        timestamp + timedelta(days=4),
        [50_000]
    )

    stage_rule = ConsistencyRules(
        0.40,
        window=ConsistencyWindow.CURRENT_STAGE
    )

    cycle_rule = ConsistencyRules(
        0.40,
        window=ConsistencyWindow.SINCE_LAST_PAYOUT
    )

    assert check_payout_consistency(
        engine.account,
        stage_rule
    )

    assert not check_payout_consistency(
        engine.account,
        cycle_rule
    )


# ==================================================
# Successful payout accounting
# ==================================================

def test_successful_payout(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [200_000])

    engine.process(
        request(
            timestamp + timedelta(days=1),
            100_000
        )
    )

    account = engine.account

    assert account.balance_cents == 2_600_000

    assert account.equity_cents == 2_600_000

    assert account.realized_pnl_cents == 200_000

    assert account.gross_withdrawals_cents == 100_000

    assert account.lifetime_gross_withdrawals_cents == 100_000

    assert account.net_cash_flow_cents == 90_000

    assert account.payout_count == 1

    assert account.lifetime_payout_count == 1

    assert account.lifetime_trader_payouts_cents == 90_000

    audit_payout_accounting(account)


def test_payout_record(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [200_000])

    payout_time = timestamp + timedelta(days=1)

    engine.process(
        request(payout_time, 100_000)
    )

    record = engine.last_payout

    assert record.timestamp == payout_time

    assert record.gross_amount_cents == 100_000

    assert record.trader_amount_cents == 90_000

    assert record.firm_amount_cents == 10_000

    assert record.balance_before_cents == 2_700_000

    assert record.balance_after_cents == 2_600_000

    assert engine.account.payout_history == [record]


def test_payout_does_not_reset_drawdown(
    make_engine,
    timestamp
):

    engine = make_engine(
        drawdown_type=DrawdownType.TRAILING_EOD,
        trailing_cap=25_100
    )

    process_days(engine, timestamp, [200_000])

    high = engine.account.high_water_mark_cents

    floor = engine.account.drawdown_floor_cents

    engine.process(
        request(
            timestamp + timedelta(days=1),
            100_000
        )
    )

    assert engine.account.high_water_mark_cents == high

    assert engine.account.drawdown_floor_cents == floor


def test_payout_with_external_expense(
    make_engine,
    timestamp
):

    engine = make_engine()

    record_cash_flow(
        engine.account,

        CashFlow(
            timestamp,
            CashFlowType.ACTIVATION,
            -5000
        )
    )

    process_days(engine, timestamp, [200_000])

    engine.process(
        request(
            timestamp + timedelta(days=1),
            100_000
        )
    )

    assert engine.account.net_cash_flow_cents == 85_000


# ==================================================
# Payout cycles
# ==================================================

def test_cycle_resets_after_payout(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [50_000, 50_000]
    )

    engine.process(
        request(timestamp + timedelta(days=2))
    )

    account = engine.account

    assert account.payout_cycle_sessions == ()

    assert account.payout_cycle_profit_cents == 0

    assert len(account.session_history) == 2

    assert account.trading_days == 2

    assert account.payout_cycle_start_index == 2


def test_new_cycle_accumulates_new_profit(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    engine.process(
        request(timestamp + timedelta(days=1))
    )

    process_days(
        engine,
        timestamp + timedelta(days=2),
        [30_000, -5000]
    )

    assert (
        engine.account.payout_cycle_profit_cents
        == 25_000
    )

    assert engine.account.realized_pnl_cents == 125_000


def test_minimum_days_reset_after_payout(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            minimum_trading_days=2
        )
    )

    process_days(
        engine,
        timestamp,
        [50_000, 50_000]
    )

    t = timestamp + timedelta(days=2)

    engine.process(request(t))

    assert_payout_rejected(
        engine,
        request(t),
        PayoutRejectionReason.MINIMUM_TRADING_DAYS
    )


# ==================================================
# Payout cooldown
# ==================================================

def test_payout_cooldown(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            minimum_days_between_payouts=7
        )
    )

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t))

    assert_payout_rejected(
        engine,

        request(t + timedelta(days=6)),

        PayoutRejectionReason.COOLDOWN
    )


def test_exact_cooldown_boundary(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            minimum_days_between_payouts=7
        )
    )

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t))

    engine.process(
        request(t + timedelta(days=7))
    )

    assert engine.account.payout_count == 2


# ==================================================
# Cumulative payout limits
# ==================================================

def test_maximum_payout_count(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            maximum_payout_count=1
        )
    )

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t))

    assert_payout_rejected(
        engine,
        request(t),
        PayoutRejectionReason.PAYOUT_COUNT_LIMIT
    )


def test_total_payout_limit(
    make_engine,
    timestamp
):

    engine = make_engine(
        payout=PayoutRules(
            profit_split=0.90,

            minimum_amount=250,

            maximum_total_payouts=1000
        )
    )

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t, 75_000))

    assert calculate_maximum_withdrawal(
        engine.account,
        engine.payout
    ) == 25_000

    engine.process(request(t, 25_000))

    assert (
        engine.account.lifetime_gross_withdrawals_cents
        == 100_000
    )

    assert_payout_rejected(
        engine,
        request(t),
        PayoutRejectionReason.ABOVE_MAXIMUM
    )


# ==================================================
# Invariants and determinism
# ==================================================

def test_assessment_does_not_modify_state(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [200_000])

    before = deepcopy(engine.account)

    engine.assess_payout(
        request(timestamp + timedelta(days=1))
    )

    assert engine.account == before


def test_audit_detects_ledger_corruption(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    engine.process(
        request(timestamp + timedelta(days=1))
    )

    engine.account.cash_flows.clear()

    with pytest.raises(AccountingInvariantError):

        audit_payout_accounting(
            engine.account
        )


def test_audit_detects_corrupted_record(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [100_000])

    engine.process(
        request(timestamp + timedelta(days=1))
    )

    record = engine.account.payout_history[0]

    engine.account.payout_history[0] = replace(
        record,

        balance_after_cents=(
            record.balance_after_cents + 1
        )
    )

    with pytest.raises(AccountingInvariantError):

        audit_payout_accounting(
            engine.account
        )


def test_multiple_payouts_accounting(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t, 50_000))

    engine.process(request(t, 50_000))

    account = engine.account

    assert account.payout_count == 2

    assert account.balance_cents == 2_600_000

    assert account.net_cash_flow_cents == 90_000

    audit_payout_accounting(account)


def test_strict_fast_equivalence(
    make_engine,
    timestamp
):

    strict = make_engine(strict=True)

    fast = make_engine(strict=False)

    for engine in (strict, fast):

        process_days(
            engine,
            timestamp,
            [50_000, 50_000, 50_000]
        )

        engine.process(
            request(
                timestamp + timedelta(days=3),
                75_000
            )
        )

        audit_payout_accounting(
            engine.account
        )

    assert strict.account == fast.account

    assert strict.last_payout == fast.last_payout


def test_payout_survives_engine_reconstruction(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(engine, timestamp, [200_000])

    t = timestamp + timedelta(days=1)

    engine.process(request(t, 50_000))

    reopened = AccountEngine(engine.account)

    reopened.process(request(t, 50_000))

    assert reopened.account.payout_count == 2

    assert reopened.account.net_cash_flow_cents == 90_000

    audit_payout_accounting(
        reopened.account
    )