import json

import pytest

from dataclasses import FrozenInstanceError, asdict

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    DailyLossRules,
    ConsistencyRules,
    TradingRules,
    TransitionMode,
    TransitionType,
    PayoutRules,
    LiveTransitionRules,
    EvaluationRules,
    FundedRules,
    LiveRules
)


# --------------------------------------------------
# Fixtures
# --------------------------------------------------

@pytest.fixture
def drawdown():

    return DrawdownRules(
        type=DrawdownType.TRAILING_EOD,
        amount=1000
    )


@pytest.fixture
def payout():

    return PayoutRules(
        profit_split=0.90,
        minimum_amount=250,
        maximum_amount=1000
    )


@pytest.fixture
def evaluation(drawdown):

    return EvaluationRules(
        profit_target=1500,
        drawdown=drawdown,
        fees=FeeRules(purchase=100),
        minimum_trading_days=3
    )


@pytest.fixture
def funded(drawdown, payout):

    return FundedRules(
        drawdown=drawdown,
        fees=FeeRules(activation=50),
        payout=payout
    )


@pytest.fixture
def live(payout):

    return LiveRules(
        drawdown=DrawdownRules(
            type=DrawdownType.STATIC,
            amount=2000
        ),
        fees=FeeRules(),
        payout=payout,
        starting_balance=25_000
    )


# --------------------------------------------------
# Payout Rules
# --------------------------------------------------

def test_default_payout_rules():

    rules = PayoutRules(
        profit_split=0.90,
        minimum_amount=250
    )

    assert rules.profit_split == 0.90
    assert rules.minimum_amount == 250
    assert rules.maximum_amount is None
    assert rules.minimum_profitable_days == 0
    assert rules.profitable_day_threshold == 0
    assert rules.minimum_trading_days == 0
    assert rules.minimum_balance_after_payout is None
    assert rules.minimum_days_between_payouts == 0
    assert rules.maximum_withdrawal_ratio is None
    assert rules.consistency is None
    assert rules.maximum_total_payouts is None
    assert rules.maximum_payout_count is None


def test_custom_payout_rules():

    rules = PayoutRules(
        profit_split=0.80,
        minimum_amount=500,
        maximum_amount=2000,
        minimum_profitable_days=5,
        profitable_day_threshold=100,
        minimum_trading_days=7,
        minimum_balance_after_payout=26_000,
        minimum_days_between_payouts=14,
        maximum_withdrawal_ratio=0.50,
        consistency=ConsistencyRules(0.30),
        maximum_total_payouts=10_000,
        maximum_payout_count=5
    )

    assert rules.profit_split == 0.80
    assert rules.minimum_amount == 500
    assert rules.maximum_amount == 2000
    assert rules.minimum_profitable_days == 5
    assert rules.profitable_day_threshold == 100
    assert rules.minimum_trading_days == 7
    assert rules.minimum_balance_after_payout == 26_000
    assert rules.minimum_days_between_payouts == 14
    assert rules.maximum_withdrawal_ratio == 0.50
    assert rules.consistency.maximum_ratio == 0.30
    assert rules.maximum_total_payouts == 10_000
    assert rules.maximum_payout_count == 5


def test_payout_immutable(payout):

    with pytest.raises(FrozenInstanceError):
        payout.profit_split = 0.50


# --------------------------------------------------
# Live Transition Rules
# --------------------------------------------------

def test_default_transition():

    rules = LiveTransitionRules(
        minimum_payout_count=5
    )

    assert rules.minimum_payout_count == 5
    assert rules.minimum_total_payouts is None
    assert rules.minimum_trading_days is None
    assert rules.minimum_profit is None
    assert rules.mode is TransitionMode.ALL

    assert (
        rules.transition_type
        is TransitionType.AUTOMATIC
    )


def test_any_transition():

    rules = LiveTransitionRules(
        minimum_payout_count=5,
        minimum_total_payouts=10_000,
        mode=TransitionMode.ANY
    )

    assert rules.minimum_payout_count == 5
    assert rules.minimum_total_payouts == 10_000
    assert rules.mode is TransitionMode.ANY


def test_manual_transition():

    rules = LiveTransitionRules(
        transition_type=TransitionType.MANUAL_REVIEW
    )

    assert (
        rules.transition_type
        is TransitionType.MANUAL_REVIEW
    )


def test_transition_enum_serialization():

    assert json.dumps(TransitionMode.ALL) == '"all"'

    assert (
        json.dumps(TransitionType.MANUAL_REVIEW)
        == '"manual_review"'
    )


# --------------------------------------------------
# Evaluation Rules
# --------------------------------------------------

def test_evaluation_configuration(evaluation):

    assert evaluation.profit_target == 1500
    assert evaluation.drawdown.amount == 1000
    assert evaluation.fees.purchase == 100
    assert evaluation.minimum_trading_days == 3


def test_evaluation_optional_rules(evaluation):

    assert evaluation.maximum_trading_days is None
    assert evaluation.maximum_calendar_days is None
    assert evaluation.daily_loss is None
    assert evaluation.consistency is None
    assert evaluation.trading is None


def test_evaluation_full_configuration(drawdown):

    rules = EvaluationRules(
        profit_target=1500,
        drawdown=drawdown,
        fees=FeeRules(purchase=100),
        minimum_trading_days=3,
        maximum_trading_days=20,
        maximum_calendar_days=30,
        daily_loss=DailyLossRules(amount=500),
        consistency=ConsistencyRules(0.40),
        trading=TradingRules(
            max_mini_contracts=2,
            max_micro_contracts=20
        )
    )

    assert rules.maximum_trading_days == 20
    assert rules.maximum_calendar_days == 30
    assert rules.daily_loss.amount == 500
    assert rules.consistency.maximum_ratio == 0.40
    assert rules.trading.max_mini_contracts == 2
    assert rules.trading.max_micro_contracts == 20


def test_evaluation_immutable(evaluation):

    with pytest.raises(FrozenInstanceError):
        evaluation.profit_target = 2000


# --------------------------------------------------
# Funded Rules
# --------------------------------------------------

def test_funded_configuration(funded):

    assert funded.drawdown.amount == 1000
    assert funded.fees.activation == 50
    assert funded.payout.profit_split == 0.90
    assert funded.payout.maximum_amount == 1000


def test_funded_optional_rules(funded):

    assert funded.daily_loss is None
    assert funded.consistency is None
    assert funded.trading is None
    assert funded.live_transition is None


def test_funded_live_transition(drawdown, payout):

    rules = FundedRules(
        drawdown=drawdown,
        fees=FeeRules(),
        payout=payout,
        live_transition=LiveTransitionRules(
            minimum_payout_count=5
        )
    )

    assert rules.live_transition.minimum_payout_count == 5


def test_funded_immutable(funded):

    with pytest.raises(FrozenInstanceError):
        funded.fees = FeeRules()


# --------------------------------------------------
# Live Rules
# --------------------------------------------------

def test_live_configuration(live):

    assert live.starting_balance == 25_000
    assert live.drawdown.amount == 2000
    assert live.payout.profit_split == 0.90


def test_live_optional_drawdown(payout):

    rules = LiveRules(
        drawdown=None,
        fees=FeeRules(),
        payout=payout
    )

    assert rules.drawdown is None
    assert rules.starting_balance is None


# --------------------------------------------------
# Root Ruleset
# --------------------------------------------------

def test_complete_ruleset(evaluation, funded, live):

    rules = Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation,
        funded=funded,
        live=live,
        max_accounts=20
    )

    assert rules.account_name == "Flex 25K"
    assert rules.account_size == 25_000
    assert rules.evaluation == evaluation
    assert rules.funded == funded
    assert rules.live == live
    assert rules.max_accounts == 20


def test_instant_funded_ruleset(funded):

    rules = Ruleset(
        account_name="Instant Funded",
        account_size=25_000,
        funded=funded
    )

    assert rules.evaluation is None
    assert rules.funded == funded
    assert rules.live is None


def test_evaluation_only_ruleset(evaluation):

    rules = Ruleset(
        account_name="Evaluation Only",
        account_size=25_000,
        evaluation=evaluation
    )

    assert rules.evaluation == evaluation
    assert rules.funded is None
    assert rules.live is None


def test_nested_immutability(evaluation):

    rules = Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation
    )

    with pytest.raises(FrozenInstanceError):
        rules.evaluation.drawdown.amount = 2000


def test_shared_ruleset(evaluation, funded):

    rules = Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation,
        funded=funded
    )

    firm_a = PropFirm("Example Firm", rules)
    firm_b = PropFirm("Example Firm", rules)

    assert firm_a.ruleset is firm_b.ruleset


def test_complete_ruleset_serialization(
    evaluation,
    funded,
    live
):

    rules = Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation,
        funded=funded,
        live=live,
        max_accounts=20
    )

    serialized = json.dumps(asdict(rules))
    restored = json.loads(serialized)

    assert restored["account_size"] == 25_000
    assert restored["max_accounts"] == 20

    assert (
        restored["evaluation"]["profit_target"]
        == 1500
    )

    assert (
        restored["evaluation"]["drawdown"]["type"]
        == "trailing_eod"
    )

    assert (
        restored["funded"]["payout"]["profit_split"]
        == 0.90
    )

    assert (
        restored["live"]["drawdown"]["type"]
        == "static"
    )

    assert restored["funded"]["live_transition"] is None


def test_ruleset_backwards_compatibility():

    rules = Ruleset("Flex 25K", 25_000)

    assert rules.account_name == "Flex 25K"
    assert rules.account_size == 25_000
    assert rules.evaluation is None
    assert rules.funded is None
    assert rules.live is None
    assert rules.max_accounts is None