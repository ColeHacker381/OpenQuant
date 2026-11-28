import pytest

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    EvaluationRules,
    FundedRules,
    LiveRules,
    PayoutRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata,
    UnresolvedRule,
    UnresolvedReason
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.enums import (
    AccountStage,
    AccountStatus,
    TerminationReason,
    CashFlowType
)

from openquant.simulation.money import (
    dollars_to_cents,
    cents_to_dollars,
    format_money
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

from openquant.simulation.account import (
    AccountDefinition,
    AccountState,
    AccountCreationError,
    SessionRecord,
    CashFlow,
    create_account
)


# ==================================================
# Fixtures
# ==================================================

@pytest.fixture
def timestamp():

    return datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )


@pytest.fixture
def revision():

    drawdown = DrawdownRules(
        DrawdownType.TRAILING_EOD,
        1000
    )

    firm = PropFirm(
        name="Example Trading",

        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,
                drawdown=drawdown,
                fees=FeeRules(purchase=100)
            ),

            funded=FundedRules(
                drawdown=drawdown,
                fees=FeeRules(activation=50),
                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250
                )
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
        require_verified=False
    )


def modify_ruleset(revision, **kwargs):

    return replace(
        revision,

        firm=replace(
            revision.firm,

            ruleset=replace(
                revision.firm.ruleset,
                **kwargs
            )
        )
    )


# ==================================================
# Monetary precision
# ==================================================

@pytest.mark.parametrize(
    "dollars,cents",
    [
        (25_000, 2_500_000),
        (1000, 100_000),
        (12.50, 1250),
        (0.01, 1),
        (0, 0),
        (-12.50, -1250),
        ("100.25", 10025),
        (Decimal("0.10"), 10)
    ]
)
def test_dollars_to_cents(dollars, cents):

    assert dollars_to_cents(dollars) == cents


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "invalid",
        0.001,
        Decimal("1.234")
    ]
)
def test_invalid_money(value):

    with pytest.raises(ValueError):

        dollars_to_cents(value)


@pytest.mark.parametrize(
    "value",
    [True, False, None, []]
)
def test_invalid_money_type(value):

    with pytest.raises(TypeError):

        dollars_to_cents(value)


def test_cents_to_dollars():

    assert cents_to_dollars(1250) == Decimal("12.50")


def test_cents_must_be_integer():

    with pytest.raises(TypeError):

        cents_to_dollars(12.5)


def test_exact_cent_arithmetic():

    result = (
        dollars_to_cents(0.1)
        + dollars_to_cents(0.2)
    )

    assert result == 30

    assert cents_to_dollars(result) == Decimal("0.30")


@pytest.mark.parametrize(
    "cents,expected",
    [
        (2_500_000, "$25,000.00"),
        (1250, "$12.50"),
        (-475, "-$4.75"),
        (0, "$0.00")
    ]
)
def test_format_money(cents, expected):

    assert format_money(cents) == expected


# ==================================================
# Account event validation
# ==================================================

def test_base_event(timestamp):

    event = AccountEvent(timestamp)

    assert event.timestamp == timestamp


def test_event_is_immutable(timestamp):

    event = EquityUpdated(
        timestamp,
        1000
    )

    with pytest.raises(FrozenInstanceError):

        event.unrealized_pnl_cents = 2000


def test_naive_timestamp_rejected():

    with pytest.raises(
        ValueError,
        match="timezone-aware"
    ):

        AccountEvent(
            datetime(2026, 1, 5)
        )


def test_invalid_timestamp_type():

    with pytest.raises(TypeError):

        AccountEvent("2026-01-05")


def test_timestamp_normalized_to_utc():

    eastern = timezone(
        timedelta(hours=-5)
    )

    event = AccountEvent(
        datetime(
            2026, 1, 5, 9, 30,
            tzinfo=eastern
        )
    )

    assert event.timestamp == datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    assert event.timestamp.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "event_type",
    [SessionStarted, SessionEnded]
)
def test_session_event(event_type, timestamp):

    event = event_type(
        timestamp,
        "2026-01-05"
    )

    assert event.session_id == "2026-01-05"


@pytest.mark.parametrize(
    "event_type",
    [SessionStarted, SessionEnded]
)
def test_empty_session_id(event_type, timestamp):

    with pytest.raises(ValueError):

        event_type(timestamp, "")


def test_equity_update(timestamp):

    event = EquityUpdated(
        timestamp,
        -12_500
    )

    assert event.unrealized_pnl_cents == -12_500


def test_equity_requires_integer_cents(timestamp):

    with pytest.raises(TypeError):

        EquityUpdated(timestamp, 12.5)


def test_realized_trade(timestamp):

    event = TradeRealized(
        timestamp=timestamp,
        gross_pnl_cents=40_000,
        commission_cents=1750
    )

    assert event.net_pnl_cents == 38_250


def test_realized_losing_trade(timestamp):

    event = TradeRealized(
        timestamp=timestamp,
        gross_pnl_cents=-25_000,
        commission_cents=250
    )

    assert event.net_pnl_cents == -25_250


def test_remaining_unrealized_pnl(timestamp):

    event = TradeRealized(
        timestamp=timestamp,
        gross_pnl_cents=10_000,
        remaining_unrealized_pnl_cents=5000
    )

    assert (
        event.remaining_unrealized_pnl_cents
        == 5000
    )


def test_negative_commission_rejected(timestamp):

    with pytest.raises(ValueError):

        TradeRealized(
            timestamp,
            gross_pnl_cents=1000,
            commission_cents=-100
        )


def test_boolean_pnl_rejected(timestamp):

    with pytest.raises(TypeError):

        TradeRealized(
            timestamp,
            gross_pnl_cents=True
        )


def test_payout_request(timestamp):

    event = PayoutRequested(
        timestamp,
        amount_cents=25_000
    )

    assert event.amount_cents == 25_000


@pytest.mark.parametrize(
    "amount",
    [0, -100]
)
def test_invalid_payout_request(timestamp, amount):

    with pytest.raises(ValueError):

        PayoutRequested(timestamp, amount)


def test_stage_activation_request(timestamp):

    event = StageActivationRequested(timestamp)

    assert event.timestamp == timestamp


# ==================================================
# Account initialization
# ==================================================

def test_create_account(account):

    assert account.account_id == "account_001"

    assert account.stage is AccountStage.EVALUATION

    assert account.status is AccountStatus.ACTIVE

    assert account.balance_cents == 2_500_000

    assert (
        account.starting_balance_cents
        == 2_500_000
    )


def test_initial_equity(account):

    assert account.equity_cents == 2_500_000

    assert account.unrealized_pnl_cents == 0


def test_initial_account_state(account):

    assert account.realized_pnl_cents == 0

    assert account.commissions_cents == 0

    assert account.trade_count == 0

    assert account.payout_count == 0

    assert account.lifetime_payout_count == 0

    assert account.termination_reason is None

    assert account.pending_stage is None

    assert account.last_event_timestamp is None

    assert not account.session_locked

    assert not account.has_active_session


def test_initial_drawdown_uninitialized(account):

    assert account.high_water_mark_cents is None

    assert account.drawdown_floor_cents is None


def test_initial_cash_flow_empty(account):

    assert account.cash_flows == []

    assert account.net_cash_flow_cents == 0


def test_account_definition_immutable(account):

    with pytest.raises(FrozenInstanceError):

        account.definition.account_id = "different"


def test_nominal_account_size(account):

    assert (
        account.definition.nominal_size_cents
        == 2_500_000
    )


def test_pinned_revision(account, revision):

    assert account.revision is revision


def test_active_stage_rules(account, revision):

    assert (
        account.stage_rules
        is revision.firm.ruleset.evaluation
    )


def test_stage_rules_missing(account):

    account.stage = AccountStage.LIVE

    with pytest.raises(RuntimeError):

        _ = account.stage_rules


def test_empty_account_id(revision):

    with pytest.raises(AccountCreationError):

        create_account(
            "",
            revision,
            require_verified=False
        )


def test_invalid_mode(revision):

    with pytest.raises(AccountCreationError):

        create_account(
            "account_001",
            revision,
            mode="invalid",
            require_verified=False
        )


def test_verification_required_by_default(revision):

    with pytest.raises(AccountCreationError):

        create_account(
            "account_001",
            revision
        )


def test_unsupported_rule_blocks_creation(revision):

    unresolved = replace(
        revision,

        metadata=replace(
            revision.metadata,

            unresolved_rules=(
                UnresolvedRule(
                    path="funded.trading.scaling",
                    reason=UnresolvedReason.UNSUPPORTED,
                    description="Unsupported scaling rule"
                ),
            )
        )
    )

    with pytest.raises(AccountCreationError):

        create_account(
            "account_001",
            unresolved,
            require_verified=False
        )


# ==================================================
# Initial stage selection
# ==================================================

def test_evaluation_only_mode(revision):

    account = create_account(
        "account_001",
        revision,
        SimulationMode.EVALUATION_ONLY,
        require_verified=False
    )

    assert account.stage is AccountStage.EVALUATION


def test_funded_only_mode(revision):

    account = create_account(
        "account_001",
        revision,
        SimulationMode.FUNDED_ONLY,
        require_verified=False
    )

    assert account.stage is AccountStage.FUNDED

    assert (
        account.stage_rules
        is revision.firm.ruleset.funded
    )


def test_instant_funded_account(revision):

    instant = modify_ruleset(
        revision,
        evaluation=None
    )

    account = create_account(
        "account_001",
        instant,
        require_verified=False
    )

    assert account.stage is AccountStage.FUNDED


def test_missing_requested_stage(revision):

    incomplete = modify_ruleset(
        revision,
        evaluation=None
    )

    with pytest.raises(AccountCreationError):

        create_account(
            "account_001",
            incomplete,
            SimulationMode.EVALUATION_ONLY,
            require_verified=False
        )


def test_direct_live_account(revision):

    live = LiveRules(
        drawdown=DrawdownRules(
            DrawdownType.STATIC,
            2000
        ),

        fees=FeeRules(),

        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=100
        ),

        starting_balance=10_000
    )

    direct_live = modify_ruleset(
        revision,
        evaluation=None,
        funded=None,
        live=live
    )

    account = create_account(
        "account_001",
        direct_live,
        require_verified=False
    )

    assert account.stage is AccountStage.LIVE

    assert account.balance_cents == 1_000_000

    assert (
        account.starting_balance_cents
        == 1_000_000
    )


# ==================================================
# Independent account states
# ==================================================

def test_accounts_share_revision(revision):

    first = create_account(
        "first",
        revision,
        require_verified=False
    )

    second = create_account(
        "second",
        revision,
        require_verified=False
    )

    assert first.revision is second.revision

    assert first is not second


def test_account_balances_independent(revision):

    first = create_account(
        "first",
        revision,
        require_verified=False
    )

    second = create_account(
        "second",
        revision,
        require_verified=False
    )

    first.balance_cents += 50_000

    assert first.balance_cents == 2_550_000

    assert second.balance_cents == 2_500_000


def test_account_histories_independent(
    revision,
    timestamp
):

    first = create_account(
        "first",
        revision,
        require_verified=False
    )

    second = create_account(
        "second",
        revision,
        require_verified=False
    )

    first.cash_flows.append(
        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            -10_000
        )
    )

    assert len(first.cash_flows) == 1

    assert second.cash_flows == []

    assert (
        first.session_history
        is not second.session_history
    )


# ==================================================
# Derived financial properties
# ==================================================

def test_positive_unrealized_equity(account):

    account.unrealized_pnl_cents = 40_000

    assert account.equity_cents == 2_540_000


def test_negative_unrealized_equity(account):

    account.unrealized_pnl_cents = -40_000

    assert account.equity_cents == 2_460_000


def test_balance_profit(account):

    account.balance_cents += 125_000

    assert account.balance_profit_cents == 125_000


def test_realized_profit_distinct_from_balance(account):

    account.realized_pnl_cents = 200_000

    account.balance_cents += 100_000

    account.gross_withdrawals_cents = 100_000

    assert account.realized_pnl_cents == 200_000

    assert account.balance_profit_cents == 100_000


def test_terminated_account_not_active(account):

    account.status = AccountStatus.TERMINATED

    account.termination_reason = (
        TerminationReason.DRAWDOWN
    )

    assert not account.is_active


def test_session_lock_preserves_account_status(account):

    account.session_locked = True

    assert account.is_active


def test_active_session(account):

    account.session_id = "2026-01-05"

    assert account.has_active_session


# ==================================================
# Session records
# ==================================================

def test_profitable_session():

    record = SessionRecord(
        session_id="2026-01-05",
        starting_balance_cents=2_500_000,
        ending_balance_cents=2_540_000,
        net_trading_pnl_cents=40_000,
        trade_count=2
    )

    assert record.was_traded

    assert record.was_profitable


def test_losing_session():

    record = SessionRecord(
        session_id="2026-01-05",
        starting_balance_cents=2_500_000,
        ending_balance_cents=2_480_000,
        net_trading_pnl_cents=-20_000,
        trade_count=1
    )

    assert record.was_traded

    assert not record.was_profitable


def test_empty_session():

    record = SessionRecord(
        session_id="2026-01-05",
        starting_balance_cents=2_500_000,
        ending_balance_cents=2_500_000,
        net_trading_pnl_cents=0,
        trade_count=0
    )

    assert not record.was_traded

    assert not record.was_profitable


def test_withdrawal_not_counted_as_trading_loss():

    record = SessionRecord(
        session_id="2026-01-05",
        starting_balance_cents=2_500_000,
        ending_balance_cents=2_480_000,
        net_trading_pnl_cents=30_000,
        trade_count=2
    )

    assert record.was_profitable


# ==================================================
# Cash flow accounting
# ==================================================

def test_purchase_cash_flow(timestamp):

    flow = CashFlow(
        timestamp,
        CashFlowType.PURCHASE,
        -10_000
    )

    assert flow.amount_cents == -10_000


def test_payout_cash_flow(timestamp):

    flow = CashFlow(
        timestamp,
        CashFlowType.PAYOUT,
        90_000
    )

    assert flow.amount_cents == 90_000


def test_invalid_expense_sign(timestamp):

    with pytest.raises(ValueError):

        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            10_000
        )


def test_invalid_payout_sign(timestamp):

    with pytest.raises(ValueError):

        CashFlow(
            timestamp,
            CashFlowType.PAYOUT,
            -90_000
        )


def test_cash_flow_requires_integer(timestamp):

    with pytest.raises(TypeError):

        CashFlow(
            timestamp,
            CashFlowType.PAYOUT,
            90.5
        )


def test_net_cash_flow(account, timestamp):

    account.cash_flows.extend([
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
            CashFlowType.PAYOUT,
            90_000
        )
    ])

    assert account.net_cash_flow_cents == 75_000

    assert (
        account.total_cash_expenses_cents
        == 15_000
    )

    assert (
        account.total_cash_received_cents
        == 90_000
    )


def test_cash_expense_does_not_change_balance(
    account,
    timestamp
):

    account.cash_flows.append(
        CashFlow(
            timestamp,
            CashFlowType.PURCHASE,
            -10_000
        )
    )

    assert account.balance_cents == 2_500_000

    assert account.net_cash_flow_cents == -10_000


def test_account_string(account):

    output = str(account)

    assert "account_001" in output

    assert "evaluation" in output

    assert "$25,000.00" in output