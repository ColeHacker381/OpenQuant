import pytest

from copy import deepcopy
from datetime import datetime, timezone, timedelta
from fractions import Fraction

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    FundedRules,
    DrawdownRules,
    DrawdownType,
    ConsistencyRules,
    DailyLossRules,
    BreachAction,
    PayoutRules,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import create_account

from openquant.simulation.enums import (
    AccountStage,
    AccountStatus,
    EvaluationOutcome,
    TerminationReason
)

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    EquityUpdated
)

from openquant.simulation.engine import (
    AccountEngine,
    InvalidAccountStateError
)

from openquant.simulation.consistency import (
    compile_consistency,
    evaluate_consistency,
    ConsistencyConfigurationError
)

from openquant.simulation.evaluation import (
    compile_evaluation,
    evaluation_calendar_days
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
        target=1250,
        ratio=0.40,
        minimum_days=3,
        maximum_days=None,
        maximum_calendar_days=None,
        drawdown=1000,
        daily_loss=None,
        mode=SimulationMode.EVALUATION_ONLY,
        strict=True
    ):

        evaluation = EvaluationRules(
            profit_target=target,

            drawdown=DrawdownRules(
                DrawdownType.STATIC,
                drawdown
            ),

            fees=FeeRules(purchase=100),

            minimum_trading_days=minimum_days,

            maximum_trading_days=maximum_days,

            maximum_calendar_days=maximum_calendar_days,

            consistency=(
                None
                if ratio is None
                else ConsistencyRules(ratio)
            ),

            daily_loss=daily_loss
        )

        funded = FundedRules(
            drawdown=DrawdownRules(
                DrawdownType.STATIC,
                1000
            ),

            fees=FeeRules(activation=50),

            payout=PayoutRules(
                profit_split=0.90,
                minimum_amount=250
            )
        )

        revision = FirmRevision(
            firm=PropFirm(
                "Example Trading",

                Ruleset(
                    account_name="25K",
                    account_size=25_000,
                    evaluation=evaluation,
                    funded=funded
                )
            ),

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


def process_day(
    engine,
    timestamp,
    pnl_cents,
    *,
    flat=True
):

    session_id = timestamp.date().isoformat()

    events = [
        SessionStarted(
            timestamp,
            session_id
        )
    ]

    if pnl_cents is not None:

        events.append(
            TradeRealized(
                timestamp + timedelta(minutes=1),
                pnl_cents
            )
        )

    events.append(
        SessionEnded(
            timestamp + timedelta(hours=1),
            session_id,
            positions_flat=flat
        )
    )

    return engine.process_all(events)


def process_days(engine, timestamp, outcomes):

    for index, pnl in enumerate(outcomes):

        if not engine.account.is_active:
            break

        process_day(
            engine,
            timestamp + timedelta(days=index),
            pnl
        )


# ==================================================
# Exact consistency mathematics
# ==================================================

def test_compile_consistency():

    result = compile_consistency(
        ConsistencyRules(0.40)
    )

    assert result == Fraction(2, 5)


@pytest.mark.parametrize(
    "ratio",
    [0, -0.1, 1.1, True, float("nan"), float("inf")]
)
def test_invalid_consistency_ratio(ratio):

    with pytest.raises(ConsistencyConfigurationError):

        compile_consistency(
            ConsistencyRules(ratio)
        )


def test_consistency_exact_boundary():

    result = evaluate_consistency(
        125_000,
        50_000,
        Fraction(2, 5)
    )

    assert result.satisfied

    assert result.actual_ratio == Fraction(2, 5)


def test_consistency_one_cent_short():

    result = evaluate_consistency(
        124_999,
        50_000,
        Fraction(2, 5)
    )

    assert not result.satisfied

    assert result.additional_profit_cents == 1


def test_consistency_one_cent_above():

    result = evaluate_consistency(
        125_001,
        50_000,
        Fraction(2, 5)
    )

    assert result.satisfied


def test_required_profit_rounds_up():

    result = evaluate_consistency(
        0,
        10_000,
        Fraction(3, 10)
    )

    assert result.required_profit_cents == 33_334


def test_consistency_requires_additional_profit():

    result = evaluate_consistency(
        125_000,
        70_000,
        Fraction(2, 5)
    )

    assert not result.satisfied

    assert result.required_profit_cents == 175_000

    assert result.additional_profit_cents == 50_000


@pytest.mark.parametrize(
    "profit",
    [0, -1000, -50_000]
)
def test_nonpositive_profit(profit):

    result = evaluate_consistency(
        profit,
        20_000,
        Fraction(2, 5)
    )

    assert result.actual_ratio is None

    assert not result.satisfied


def test_zero_profit_and_zero_best_day():

    result = evaluate_consistency(
        0,
        0,
        Fraction(2, 5)
    )

    assert not result.satisfied

    assert result.additional_profit_cents == 1


# ==================================================
# Evaluation initialization
# ==================================================

def test_initial_evaluation_outcome(make_engine):

    engine = make_engine()

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.IN_PROGRESS
    )

    assert engine.account.evaluation_started_at is None

    assert engine.account.evaluation_completed_at is None


def test_compiled_evaluation(make_engine):

    engine = make_engine()

    rules = engine.evaluation

    assert rules.profit_target_cents == 125_000

    assert rules.minimum_trading_days == 3

    assert rules.consistency_ratio == Fraction(2, 5)


def test_funded_only_has_no_evaluation(make_engine):

    engine = make_engine(
        mode=SimulationMode.FUNDED_ONLY
    )

    assert engine.evaluation is None

    assert engine.evaluation_progress is None

    assert engine.account.evaluation_outcome is None


def test_evaluation_start_timestamp(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        SessionStarted(timestamp, "day_1")
    )

    assert (
        engine.account.evaluation_started_at
        == timestamp
    )


def test_initial_progress(make_engine):

    engine = make_engine()

    progress = engine.evaluation_progress

    assert progress.net_profit_cents == 0

    assert progress.additional_profit_cents == 125_000

    assert progress.remaining_trading_days == 3

    assert not progress.eligible


# ==================================================
# Profit target and minimum trading days
# ==================================================

def test_profit_target_not_reached(
    make_engine,
    timestamp
):

    engine = make_engine(ratio=None)

    process_days(
        engine,
        timestamp,
        [30_000, 30_000, 30_000]
    )

    assert engine.account.is_active

    assert not engine.evaluation_progress.target_satisfied


def test_minimum_days_not_satisfied(
    make_engine,
    timestamp
):

    engine = make_engine(ratio=None)

    process_day(
        engine,
        timestamp,
        150_000
    )

    assert engine.evaluation_progress.target_satisfied

    assert not engine.evaluation_progress.days_satisfied

    assert engine.account.is_active


def test_empty_sessions_do_not_count(
    make_engine,
    timestamp
):

    engine = make_engine(ratio=None)

    process_days(
        engine,
        timestamp,
        [150_000, None, None]
    )

    assert engine.account.completed_sessions == 3

    assert engine.account.trading_days == 1

    assert engine.account.is_active


def test_breakeven_trade_counts_as_trading_day(
    make_engine,
    timestamp
):

    engine = make_engine(ratio=None)

    process_days(
        engine,
        timestamp,
        [150_000, 0, 0]
    )

    assert engine.account.trading_days == 3

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.PASSED
    )


def test_commissions_reduce_eligible_profit(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(
            timestamp,
            gross_pnl_cents=125_000,
            commission_cents=500
        ),

        SessionEnded(
            timestamp,
            "day_1",
            positions_flat=True
        )
    ])

    assert engine.account.realized_pnl_cents == 124_500

    assert not engine.evaluation_progress.target_satisfied

    assert engine.account.is_active


# ==================================================
# Consistency integration
# ==================================================

def test_consistency_prevents_pass(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [70_000, 30_000, 25_000]
    )

    progress = engine.evaluation_progress

    assert progress.target_satisfied

    assert progress.days_satisfied

    assert not progress.consistency_satisfied

    assert progress.effective_target_cents == 175_000

    assert progress.additional_profit_cents == 50_000

    assert engine.account.is_active


def test_additional_profit_satisfies_consistency(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [70_000, 30_000, 25_000, 50_000]
    )

    assert engine.evaluation_progress.eligible

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.PASSED
    )


def test_new_best_day_increases_required_profit(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [70_000, 30_000, 25_000, 200_000]
    )

    progress = engine.evaluation_progress

    assert progress.net_profit_cents == 325_000

    assert progress.effective_target_cents == 500_000

    assert progress.additional_profit_cents == 175_000

    assert not progress.consistency_satisfied


def test_losing_days_reduce_consistency_denominator(
    make_engine,
    timestamp
):

    engine = make_engine(minimum_days=5)

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 50_000, -25_000]
    )

    progress = engine.evaluation_progress

    assert progress.net_profit_cents == 125_000

    assert (
        progress.consistency.actual_ratio
        == Fraction(2, 5)
    )


def test_no_consistency_rule(make_engine):

    engine = make_engine(ratio=None)

    assert engine.evaluation_progress.consistency is None

    assert engine.evaluation_progress.consistency_satisfied


# ==================================================
# Session completion and flat confirmation
# ==================================================

def test_no_intraday_pass(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=0
    )

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(timestamp, 150_000)
    ])

    assert engine.account.is_active

    assert engine.evaluation_progress.net_profit_cents == 0


@pytest.mark.parametrize(
    "flat",
    [None, False]
)
def test_flat_confirmation_required(
    make_engine,
    timestamp,
    flat
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    process_day(
        engine,
        timestamp,
        150_000,
        flat=flat
    )

    assert engine.evaluation_progress.eligible

    assert engine.account.is_active


def test_unrealized_pnl_prevents_pass(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(
            timestamp,
            gross_pnl_cents=150_000,
            remaining_unrealized_pnl_cents=1000
        ),

        SessionEnded(
            timestamp,
            "day_1",
            positions_flat=True
        )
    ])

    assert engine.account.is_active


def test_invalid_flat_confirmation(timestamp):

    with pytest.raises(TypeError):

        SessionEnded(
            timestamp,
            "day_1",
            positions_flat="yes"
        )


# ==================================================
# Successful evaluation completion
# ==================================================

def test_evaluation_only_pass(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 25_000]
    )

    account = engine.account

    assert (
        account.evaluation_outcome
        is EvaluationOutcome.PASSED
    )

    assert account.status is AccountStatus.CLOSED

    assert account.pending_stage is None

    assert account.termination_reason is None

    assert account.stage is AccountStage.EVALUATION


def test_full_lifecycle_pass_awaits_activation(
    make_engine,
    timestamp
):

    engine = make_engine(
        mode=SimulationMode.FULL_LIFECYCLE
    )

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 25_000]
    )

    account = engine.account

    assert (
        account.status
        is AccountStatus.AWAITING_ACTIVATION
    )

    assert account.pending_stage is AccountStage.FUNDED

    assert account.stage is AccountStage.EVALUATION

    assert account.balance_cents == 2_625_000


def test_pass_completion_timestamp(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    process_day(
        engine,
        timestamp,
        125_000
    )

    assert (
        engine.account.evaluation_completed_at
        == timestamp + timedelta(hours=1)
    )


def test_passed_account_rejects_additional_events(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    process_day(engine, timestamp, 125_000)

    before = deepcopy(engine.account)

    with pytest.raises(InvalidAccountStateError):

        engine.process(
            SessionStarted(
                timestamp + timedelta(days=1),
                "day_2"
            )
        )

    assert engine.account == before


def test_batch_stops_on_pass(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    def events():

        yield SessionStarted(timestamp, "day_1")

        yield TradeRealized(timestamp, 125_000)

        yield SessionEnded(
            timestamp,
            "day_1",
            positions_flat=True
        )

        pytest.fail(
            "Consumed an event after evaluation passed"
        )

    assert engine.process_all(events()) == 3


# ==================================================
# Trading-day expiration
# ==================================================

def test_maximum_trading_days_expiration(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1,
        maximum_days=3
    )

    process_days(
        engine,
        timestamp,
        [10_000, 10_000, 10_000]
    )

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.EXPIRATION
    )


def test_pass_on_final_permitted_trading_day(
    make_engine,
    timestamp
):

    engine = make_engine(
        maximum_days=3
    )

    process_days(
        engine,
        timestamp,
        [50_000, 50_000, 25_000]
    )

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.PASSED
    )


def test_consistency_failure_on_final_day(
    make_engine,
    timestamp
):

    engine = make_engine(
        maximum_days=3
    )

    process_days(
        engine,
        timestamp,
        [70_000, 30_000, 25_000]
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.EXPIRATION
    )


def test_empty_sessions_do_not_exhaust_trading_days(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1,
        maximum_days=2
    )

    process_days(
        engine,
        timestamp,
        [10_000, None, None]
    )

    assert engine.account.is_active

    assert engine.account.trading_days == 1


# ==================================================
# Calendar expiration
# ==================================================

def test_calendar_day_count(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_day(engine, timestamp, 10_000)

    assert evaluation_calendar_days(
        engine.account,
        timestamp
    ) == 1

    assert evaluation_calendar_days(
        engine.account,
        timestamp + timedelta(days=4)
    ) == 5


def test_calendar_expiration_after_gap(
    make_engine,
    timestamp
):

    engine = make_engine(
        minimum_days=1,
        maximum_calendar_days=3
    )

    process_day(engine, timestamp, 10_000)

    processed = process_day(
        engine,
        timestamp + timedelta(days=3),
        200_000
    )

    assert processed == 1

    assert engine.account.trade_count == 1

    assert (
        engine.account.termination_reason
        is TerminationReason.EXPIRATION
    )


def test_final_calendar_day_permitted(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1,
        maximum_calendar_days=3
    )

    process_day(engine, timestamp, 10_000)

    process_day(
        engine,
        timestamp + timedelta(days=2),
        125_000
    )

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.PASSED
    )


# ==================================================
# Failure precedence
# ==================================================

def test_drawdown_records_failed_evaluation(
    make_engine,
    timestamp
):

    engine = make_engine()

    process_day(
        engine,
        timestamp,
        -100_000
    )

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.DRAWDOWN
    )


def test_daily_loss_records_failed_evaluation(
    make_engine,
    timestamp
):

    engine = make_engine(
        daily_loss=DailyLossRules(
            amount=500,
            action=BreachAction.TERMINATE
        )
    )

    process_day(
        engine,
        timestamp,
        -50_000
    )

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.DAILY_LOSS
    )


def test_daily_lock_does_not_fail_evaluation(
    make_engine,
    timestamp
):

    engine = make_engine(
        daily_loss=DailyLossRules(
            amount=500,
            action=BreachAction.LOCK_SESSION
        )
    )

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(timestamp, -50_000)
    ])

    assert engine.account.session_locked

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.IN_PROGRESS
    )


def test_target_reached_then_drawdown_breached(
    make_engine,
    timestamp
):

    engine = make_engine(
        ratio=None,
        minimum_days=1
    )

    engine.process_all([
        SessionStarted(timestamp, "day_1"),

        TradeRealized(timestamp, 150_000),

        TradeRealized(timestamp, -250_000),

        SessionEnded(
            timestamp,
            "day_1",
            positions_flat=True
        )
    ])

    assert (
        engine.account.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.DRAWDOWN
    )


# ==================================================
# Determinism and strict mode
# ==================================================

@pytest.mark.parametrize(
    "outcomes",
    [
        [50_000, 50_000, 25_000],
        [-100_000],
        [70_000, 30_000, 25_000]
    ]
)
def test_strict_fast_equivalence(
    make_engine,
    timestamp,
    outcomes
):

    strict = make_engine(strict=True)

    fast = make_engine(strict=False)

    process_days(strict, timestamp, outcomes)

    process_days(fast, timestamp, outcomes)

    assert strict.account == fast.account

    assert (
        strict.evaluation_progress
        == fast.evaluation_progress
    )