import pytest

from copy import deepcopy
from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    DailyLossRules,
    BalanceReference,
    BreachAction,
    TradingRules,
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

from openquant.simulation.engine import (
    AccountEngine,
    EventProcessingError
)

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    EquityUpdated
)

from openquant.simulation.daily_loss import (
    DailyLossConfigurationError,
    compile_daily_loss,
    calculate_daily_loss,
    calculate_daily_loss_floor,
    is_daily_loss_breached
)

from openquant.simulation.trading import (
    PositionExposure,
    TradingRequest,
    TradingRejectionReason,
    is_quantity_reduction,
    is_reduce_only
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
def make_engine(timestamp):

    def factory(
        *,
        daily_amount=500,
        action=BreachAction.LOCK_SESSION,
        reference=BalanceReference.EQUITY,
        breach_on_equal=True,
        drawdown_amount=1000,
        trading=None,
        enable_daily=True,
        start_session=True,
        strict=True
    ):

        daily_loss = None

        if enable_daily:

            daily_loss = DailyLossRules(
                amount=daily_amount,
                action=action,
                reference=reference,
                breach_on_equal=breach_on_equal
            )

        firm = PropFirm(
            "Example Trading",

            Ruleset(
                account_name="25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,

                    drawdown=DrawdownRules(
                        DrawdownType.STATIC,
                        drawdown_amount
                    ),

                    fees=FeeRules(),

                    daily_loss=daily_loss,

                    trading=trading
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
            SimulationMode.EVALUATION_ONLY,
            require_verified=False
        )

        engine = AccountEngine(
            account,
            strict=strict
        )

        if start_session:

            engine.process(
                SessionStarted(
                    timestamp,
                    "day_1"
                )
            )

        return engine

    return factory


@pytest.fixture
def trading_rules():

    return TradingRules(
        max_mini_contracts=2,
        max_micro_contracts=20,
        allow_news=False,
        allow_overnight=False,
        allow_weekend=False
    )


def make_request(
    *,
    current_mini=0,
    current_micro=0,
    proposed_mini=0,
    proposed_micro=0,
    news=False,
    overnight=False,
    weekend=False
):

    return TradingRequest(
        current=PositionExposure(
            current_mini,
            current_micro
        ),

        proposed=PositionExposure(
            proposed_mini,
            proposed_micro
        ),

        during_news=news,

        requires_overnight=overnight,

        requires_weekend=weekend
    )


# ==================================================
# Daily loss compilation
# ==================================================

def test_compile_daily_loss():

    rules = DailyLossRules(
        amount=500,
        action=BreachAction.LOCK_SESSION
    )

    compiled = compile_daily_loss(rules)

    assert compiled.amount_cents == 50_000

    assert (
        compiled.action
        is BreachAction.LOCK_SESSION
    )

    assert (
        compiled.reference
        is BalanceReference.EQUITY
    )


@pytest.mark.parametrize(
    "amount",
    [0, -100, True, float("inf"), 100.001]
)
def test_invalid_daily_loss_amount(amount):

    with pytest.raises(DailyLossConfigurationError):

        compile_daily_loss(
            DailyLossRules(amount=amount)
        )


def test_no_daily_loss_configuration(make_engine):

    engine = make_engine(
        enable_daily=False
    )

    assert engine.daily_loss is None

    assert engine.account.daily_loss_floor_cents is None

    assert (
        engine.account.remaining_daily_loss_cents
        is None
    )


# ==================================================
# Daily loss mathematics
# ==================================================

def test_initial_daily_loss_floor(make_engine):

    engine = make_engine()

    assert calculate_daily_loss_floor(
        engine.account,
        engine.daily_loss
    ) == 2_450_000

    assert calculate_daily_loss(
        engine.account,
        engine.daily_loss
    ) == 0


def test_daily_loss_distance(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -20_000)
    )

    assert calculate_daily_loss(
        engine.account,
        engine.daily_loss
    ) == 20_000

    assert (
        engine.account.remaining_daily_loss_cents
        == 30_000
    )


def test_daily_loss_does_not_trail(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        TradeRealized(timestamp, 40_000),
        TradeRealized(timestamp, -60_000)
    ])

    account = engine.account

    assert account.balance_cents == 2_480_000

    assert account.daily_loss_floor_cents == 2_450_000

    assert calculate_daily_loss(
        account,
        engine.daily_loss
    ) == 20_000

    assert not account.session_locked


def test_daily_profit_is_not_negative_loss(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, 30_000)
    )

    assert calculate_daily_loss(
        engine.account,
        engine.daily_loss
    ) == 0


def test_no_active_session_daily_metrics(make_engine):

    engine = make_engine(
        start_session=False
    )

    assert (
        engine.account.daily_loss_floor_cents
        is None
    )

    assert not is_daily_loss_breached(
        engine.account,
        engine.daily_loss
    )


# ==================================================
# Exact boundary behavior
# ==================================================

@pytest.mark.parametrize(
    "loss,breach_on_equal,locked",
    [
        (49_999, True, False),
        (50_000, True, True),
        (50_001, True, True),

        (49_999, False, False),
        (50_000, False, False),
        (50_001, False, True)
    ]
)
def test_daily_loss_boundary(
    make_engine,
    timestamp,
    loss,
    breach_on_equal,
    locked
):

    engine = make_engine(
        breach_on_equal=breach_on_equal
    )

    engine.process(
        TradeRealized(timestamp, -loss)
    )

    assert engine.account.session_locked is locked

    assert engine.account.is_active


def test_commission_triggers_daily_loss(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(
            timestamp,
            gross_pnl_cents=-49_900,
            commission_cents=100
        )
    )

    assert engine.account.session_locked

    assert engine.account.realized_pnl_cents == -50_000


# ==================================================
# Balance and equity references
# ==================================================

def test_equity_based_daily_loss(
    make_engine,
    timestamp
):

    engine = make_engine(
        reference=BalanceReference.EQUITY
    )

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    assert engine.account.balance_cents == 2_500_000

    assert engine.account.session_locked


def test_balance_based_ignores_unrealized_loss(
    make_engine,
    timestamp
):

    engine = make_engine(
        reference=BalanceReference.BALANCE
    )

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    assert not engine.account.session_locked


def test_balance_based_realized_loss(
    make_engine,
    timestamp
):

    engine = make_engine(
        reference=BalanceReference.BALANCE
    )

    engine.process(
        TradeRealized(timestamp, -50_000)
    )

    assert engine.account.session_locked


def test_equity_reference_at_session_start(
    make_engine,
    timestamp
):

    engine = make_engine(
        start_session=False,
        reference=BalanceReference.EQUITY
    )

    engine.account.unrealized_pnl_cents = -10_000

    engine.process(
        SessionStarted(timestamp, "day_1")
    )

    assert (
        engine.account.daily_loss_floor_cents
        == 2_440_000
    )

    engine.process(
        EquityUpdated(timestamp, -60_000)
    )

    assert engine.account.session_locked


# ==================================================
# Daily loss termination and locks
# ==================================================

def test_daily_loss_terminates(
    make_engine,
    timestamp
):

    engine = make_engine(
        action=BreachAction.TERMINATE
    )

    engine.process(
        TradeRealized(timestamp, -50_000)
    )

    assert not engine.account.is_active

    assert (
        engine.account.termination_reason
        is TerminationReason.DAILY_LOSS
    )


def test_daily_loss_lock_preserves_account(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -50_000)
    )

    assert engine.account.is_active

    assert engine.account.session_locked

    assert engine.account.termination_reason is None


def test_lock_does_not_reset_after_recovery(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        EquityUpdated(timestamp, -50_000),
        EquityUpdated(timestamp, -10_000)
    ])

    assert engine.account.session_locked

    assert engine.account.is_active


def test_locked_account_accepts_existing_fill(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        EquityUpdated(timestamp, -50_000),

        TradeRealized(
            timestamp,
            gross_pnl_cents=-60_000
        )
    ])

    assert engine.account.balance_cents == 2_440_000

    assert engine.account.session_locked

    assert engine.account.trade_count == 1


def test_session_lock_clears_at_session_end(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        TradeRealized(timestamp, -50_000),

        SessionEnded(timestamp, "day_1")
    ])

    assert not engine.account.session_locked

    assert not engine.account.has_active_session


def test_new_session_has_new_daily_floor(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        TradeRealized(timestamp, -50_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        )
    ])

    assert engine.account.is_active

    assert not engine.account.session_locked

    assert (
        engine.account.daily_loss_floor_cents
        == 2_400_000
    )

    assert (
        engine.account.remaining_daily_loss_cents
        == 50_000
    )


def test_second_session_can_lock_again(
    make_engine,
    timestamp
):

    engine = make_engine(
        drawdown_amount=2000
    )

    engine.process_all([
        TradeRealized(timestamp, -50_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        ),

        TradeRealized(
            timestamp + timedelta(days=1),
            -50_000
        )
    ])

    assert engine.account.session_locked

    assert engine.account.is_active

    assert (
        engine.last_daily_loss_breach.session_id
        == "day_2"
    )


# ==================================================
# Rule precedence
# ==================================================

def test_drawdown_takes_precedence(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    assert (
        engine.account.termination_reason
        is TerminationReason.DRAWDOWN
    )

    assert engine.last_drawdown_breach is not None

    assert engine.last_daily_loss_breach is None


def test_drawdown_enforced_after_session_lock(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        EquityUpdated(timestamp, -50_000),

        EquityUpdated(timestamp, -110_000)
    ])

    assert not engine.account.is_active

    assert (
        engine.account.termination_reason
        is TerminationReason.DRAWDOWN
    )


def test_disabled_daily_loss_does_not_lock(
    make_engine,
    timestamp
):

    engine = make_engine(
        enable_daily=False
    )

    engine.process(
        TradeRealized(timestamp, -60_000)
    )

    assert engine.account.is_active

    assert not engine.account.session_locked


# ==================================================
# Breach diagnostic records
# ==================================================

def test_daily_loss_breach_record(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -60_000)
    )

    breach = engine.last_daily_loss_breach

    assert breach.timestamp == timestamp

    assert breach.session_id == "day_1"

    assert breach.starting_value_cents == 2_500_000

    assert breach.monitored_value_cents == 2_440_000

    assert breach.floor_cents == 2_450_000

    assert breach.loss_cents == 60_000

    assert breach.overshoot_cents == 10_000


def test_lock_preserves_first_breach(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    breach = engine.last_daily_loss_breach

    engine.process(
        EquityUpdated(timestamp, -60_000)
    )

    assert engine.last_daily_loss_breach is breach


# ==================================================
# Effective risk distance
# ==================================================

def test_effective_distance(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -20_000)
    )

    assert (
        engine.account.remaining_drawdown_cents
        == 80_000
    )

    assert (
        engine.account.remaining_daily_loss_cents
        == 30_000
    )

    assert (
        engine.account.effective_loss_distance_cents
        == 30_000
    )


def test_effective_distance_without_daily_limit(
    make_engine
):

    engine = make_engine(
        enable_daily=False
    )

    assert (
        engine.account.effective_loss_distance_cents
        == 100_000
    )


# ==================================================
# Exposure and request validation
# ==================================================

def test_flat_exposure():

    assert PositionExposure().is_flat


def test_short_exposure():

    exposure = PositionExposure(
        mini_contracts=-2
    )

    assert not exposure.is_flat


@pytest.mark.parametrize(
    "value",
    [True, 1.5, "2", None]
)
def test_invalid_contract_quantity(value):

    with pytest.raises(TypeError):

        PositionExposure(
            mini_contracts=value
        )


def test_invalid_trading_context():

    with pytest.raises(TypeError):

        TradingRequest(
            current=PositionExposure(),
            proposed=PositionExposure(1),
            during_news=None,
            requires_overnight=False,
            requires_weekend=False
        )


# ==================================================
# Reduce-only classification
# ==================================================

@pytest.mark.parametrize(
    "current,proposed,expected",
    [
        (2, 1, True),
        (2, 0, True),
        (-3, -2, True),
        (-3, 0, True),
        (2, 2, True),
        (0, 0, True),

        (0, 1, False),
        (0, -1, False),
        (1, 2, False),
        (-1, -2, False),
        (2, -1, False),
        (-2, 1, False)
    ]
)
def test_quantity_reduction(
    current,
    proposed,
    expected
):

    assert is_quantity_reduction(
        current,
        proposed
    ) is expected


def test_combined_exposure_reduction():

    request = make_request(
        current_mini=2,
        current_micro=10,
        proposed_mini=1,
        proposed_micro=5
    )

    assert is_reduce_only(request)


def test_reduction_cannot_open_other_instrument():

    request = make_request(
        current_mini=2,
        proposed_mini=1,
        proposed_micro=5
    )

    assert not is_reduce_only(request)


# ==================================================
# Trading authorization
# ==================================================

def test_authorization_within_limit(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(proposed_mini=2)
    )

    assert decision.allowed


@pytest.mark.parametrize(
    "quantity",
    [3, -3]
)
def test_mini_position_limit(
    make_engine,
    trading_rules,
    quantity
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_mini=quantity
        )
    )

    assert not decision.allowed

    assert (
        TradingRejectionReason.MINI_LIMIT
        in decision.reasons
    )


@pytest.mark.parametrize(
    "quantity,allowed",
    [
        (20, True),
        (-20, True),
        (21, False),
        (-21, False)
    ]
)
def test_micro_position_limit(
    make_engine,
    trading_rules,
    quantity,
    allowed
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_micro=quantity
        )
    )

    assert decision.allowed is allowed


def test_resulting_exposure_checked(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            current_mini=1,
            proposed_mini=3
        )
    )

    assert not decision.allowed


def test_mixed_exposure_rejected(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_mini=1,
            proposed_micro=5
        )
    )

    assert (
        TradingRejectionReason
        .MIXED_EXPOSURE_UNSUPPORTED
        in decision.reasons
    )


def test_missing_session_rejects_request(make_engine):

    engine = make_engine(
        start_session=False
    )

    decision = engine.authorize_trade(
        make_request(proposed_mini=1)
    )

    assert (
        TradingRejectionReason.NO_ACTIVE_SESSION
        in decision.reasons
    )


def test_terminated_account_rejects_request(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -100_000)
    )

    decision = engine.authorize_trade(
        make_request(proposed_mini=1)
    )

    assert (
        TradingRejectionReason.ACCOUNT_INACTIVE
        in decision.reasons
    )


# ==================================================
# Session lock authorization
# ==================================================

def test_locked_account_rejects_new_position(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        TradeRealized(timestamp, -50_000)
    )

    decision = engine.authorize_trade(
        make_request(proposed_mini=1)
    )

    assert not decision.allowed

    assert (
        TradingRejectionReason.SESSION_LOCKED
        in decision.reasons
    )


def test_locked_account_allows_closing_position(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    decision = engine.authorize_trade(
        make_request(
            current_mini=2,
            proposed_mini=0
        )
    )

    assert decision.allowed


def test_locked_account_allows_partial_reduction(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    decision = engine.authorize_trade(
        make_request(
            current_micro=10,
            proposed_micro=5
        )
    )

    assert decision.allowed


def test_locked_account_rejects_reversal(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process(
        EquityUpdated(timestamp, -50_000)
    )

    decision = engine.authorize_trade(
        make_request(
            current_mini=2,
            proposed_mini=-1
        )
    )

    assert not decision.allowed

    assert (
        TradingRejectionReason.SESSION_LOCKED
        in decision.reasons
    )


def test_risk_reduction_allowed_above_limit(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            current_mini=5,
            proposed_mini=4
        )
    )

    assert decision.allowed


def test_new_session_restores_authorization(
    make_engine,
    timestamp
):

    engine = make_engine()

    engine.process_all([
        TradeRealized(timestamp, -50_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        )
    ])

    decision = engine.authorize_trade(
        make_request(proposed_mini=1)
    )

    assert decision.allowed


# ==================================================
# Trading restrictions
# ==================================================

@pytest.mark.parametrize(
    "context,reason",
    [
        (
            {"news": True},
            TradingRejectionReason.NEWS_RESTRICTED
        ),
        (
            {"overnight": True},
            TradingRejectionReason.OVERNIGHT_RESTRICTED
        ),
        (
            {"weekend": True},
            TradingRejectionReason.WEEKEND_RESTRICTED
        )
    ]
)
def test_trading_restrictions(
    make_engine,
    trading_rules,
    context,
    reason
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_mini=1,
            **context
        )
    )

    assert not decision.allowed

    assert reason in decision.reasons


def test_news_allowed_when_configured(make_engine):

    engine = make_engine(
        trading=TradingRules(
            allow_news=True
        )
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_mini=1,
            news=True
        )
    )

    assert decision.allowed


def test_closing_position_during_news(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            current_mini=1,
            proposed_mini=0,
            news=True
        )
    )

    assert decision.allowed


def test_multiple_rejection_reasons(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    decision = engine.authorize_trade(
        make_request(
            proposed_mini=3,
            news=True
        )
    )

    assert set(decision.reasons) == {
        TradingRejectionReason.MINI_LIMIT,
        TradingRejectionReason.NEWS_RESTRICTED
    }


def test_authorization_does_not_modify_account(
    make_engine,
    trading_rules
):

    engine = make_engine(
        trading=trading_rules
    )

    before = deepcopy(engine.account)

    count = engine.processed_event_count

    engine.authorize_trade(
        make_request(proposed_mini=3)
    )

    assert engine.account == before

    assert engine.processed_event_count == count


# ==================================================
# Integration and determinism
# ==================================================

def test_rejected_event_preserves_risk_state(
    make_engine,
    timestamp
):

    engine = make_engine()

    before = deepcopy(engine.account)

    with pytest.raises(EventProcessingError):

        engine.process(
            SessionEnded(
                timestamp,
                "incorrect_session"
            )
        )

    assert engine.account == before

    assert engine.last_daily_loss_breach is None


@pytest.mark.parametrize(
    "strict",
    [True, False]
)
def test_daily_loss_enforced_in_both_modes(
    make_engine,
    timestamp,
    strict
):

    engine = make_engine(
        strict=strict
    )

    engine.process(
        TradeRealized(timestamp, -50_000)
    )

    assert engine.account.session_locked


def test_strict_fast_equivalence(
    make_engine,
    timestamp
):

    strict = make_engine(strict=True)

    fast = make_engine(strict=False)

    events = [
        EquityUpdated(timestamp, -50_000),

        TradeRealized(timestamp, -60_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        ),

        TradeRealized(
            timestamp + timedelta(days=1),
            -40_000
        )
    ]

    strict.process_all(events)

    fast.process_all(events)

    assert strict.account == fast.account

    assert (
        strict.last_daily_loss_breach
        == fast.last_daily_loss_breach
    )

    assert (
        strict.last_drawdown_breach
        == fast.last_drawdown_breach
    )


def test_batch_continues_after_session_lock(
    make_engine,
    timestamp
):

    engine = make_engine()

    processed = engine.process_all([
        TradeRealized(timestamp, -50_000),

        SessionEnded(timestamp, "day_1"),

        SessionStarted(
            timestamp + timedelta(days=1),
            "day_2"
        )
    ])

    assert processed == 3

    assert engine.account.is_active

    assert not engine.account.session_locked