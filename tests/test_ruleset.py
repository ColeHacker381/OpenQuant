import json
import pytest

from dataclasses import FrozenInstanceError, asdict

from openquant.propfirm.ruleset import (
    DrawdownType,
    BalanceReference,
    BreachAction,
    ConsistencyWindow,
    DrawdownRules,
    FeeRules,
    DailyLossRules,
    ConsistencyRules,
    TradingRules
)

def test_drawdown_type_values():

    assert DrawdownType.STATIC.value == "static"

    assert (
        DrawdownType.TRAILING_EOD.value
        == "trailing_eod"
    )

    assert (
        DrawdownType.TRAILING_INTRADAY.value
        == "trailing_intraday"
    )


def test_enum_from_string():

    assert (
        DrawdownType("static")
        is DrawdownType.STATIC
    )


def test_invalid_enum():

    with pytest.raises(ValueError):
        DrawdownType("invalid_drawdown")


def test_enum_json_compatible():

    assert json.dumps(DrawdownType.STATIC) == '"static"'

def test_static_drawdown():

    rules = DrawdownRules(
        type=DrawdownType.STATIC,
        amount=1000
    )

    assert rules.type == DrawdownType.STATIC
    assert rules.amount == 1000
    assert rules.trailing_stop_balance is None

    assert rules.reference == BalanceReference.BALANCE

    assert (
        rules.breach_reference
        == BalanceReference.EQUITY
    )

    assert rules.breach_on_equal is True


def test_trailing_drawdown():

    rules = DrawdownRules(
        type=DrawdownType.TRAILING_INTRADAY,
        amount=1500,
        trailing_stop_balance=50000,
        reference=BalanceReference.EQUITY
    )

    assert rules.amount == 1500
    assert rules.trailing_stop_balance == 50000
    assert rules.reference == BalanceReference.EQUITY


def test_drawdown_immutable():

    rules = DrawdownRules(
        type=DrawdownType.STATIC,
        amount=1000
    )

    with pytest.raises(FrozenInstanceError):
        rules.amount = 2000

def test_default_fees():

    fees = FeeRules()

    assert fees.purchase == 0
    assert fees.activation == 0
    assert fees.recurring == 0
    assert fees.recurring_interval_days is None
    assert fees.reset is None


def test_custom_fees():

    fees = FeeRules(
        purchase=100,
        activation=50,
        recurring=25,
        recurring_interval_days=30,
        reset=75
    )

    assert fees.purchase == 100
    assert fees.activation == 50
    assert fees.recurring == 25
    assert fees.recurring_interval_days == 30
    assert fees.reset == 75


def test_free_reset_vs_unavailable():

    free_reset = FeeRules(reset=0)
    unavailable = FeeRules(reset=None)

    assert free_reset.reset == 0
    assert unavailable.reset is None

    assert free_reset != unavailable


def test_default_daily_loss():

    rules = DailyLossRules(amount=500)

    assert rules.amount == 500

    assert rules.action == BreachAction.TERMINATE

    assert rules.reference == BalanceReference.EQUITY


def test_daily_loss_session_lock():

    rules = DailyLossRules(
        amount=500,
        action=BreachAction.LOCK_SESSION
    )

    assert rules.action == BreachAction.LOCK_SESSION


def test_default_consistency():

    rules = ConsistencyRules(
        maximum_ratio=0.40
    )

    assert rules.maximum_ratio == 0.40

    assert (
        rules.window
        == ConsistencyWindow.CURRENT_STAGE
    )


def test_payout_consistency():

    rules = ConsistencyRules(
        maximum_ratio=0.30,
        window=ConsistencyWindow.SINCE_LAST_PAYOUT
    )

    assert rules.maximum_ratio == 0.30

    assert (
        rules.window
        == ConsistencyWindow.SINCE_LAST_PAYOUT
    )


def test_default_trading_rules():

    rules = TradingRules()

    assert rules.max_mini_contracts is None
    assert rules.max_micro_contracts is None
    assert rules.allow_overnight is False
    assert rules.allow_weekend is False
    assert rules.allow_news is True


def test_custom_trading_rules():

    rules = TradingRules(
        max_mini_contracts=2,
        max_micro_contracts=20,
        allow_news=False
    )

    assert rules.max_mini_contracts == 2
    assert rules.max_micro_contracts == 20
    assert rules.allow_news is False


def test_drawdown_json_serialization():

    rules = DrawdownRules(
        type=DrawdownType.TRAILING_EOD,
        amount=1000,
        trailing_stop_balance=25000
    )

    data = asdict(rules)

    serialized = json.dumps(data)

    restored = json.loads(serialized)

    assert restored["type"] == "trailing_eod"
    assert restored["amount"] == 1000

    assert restored["trailing_stop_balance"] == 25000
    assert restored["reference"] == "balance"
    assert restored["breach_reference"] == "equity"
    assert restored["breach_on_equal"] is True

