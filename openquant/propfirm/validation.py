import pytest

from dataclasses import replace

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    DailyLossRules,
    ConsistencyRules,
    TradingRules,
    PayoutRules,
    LiveTransitionRules,
    EvaluationRules,
    FundedRules,
    LiveRules,
    TransitionType
)

from openquant.propfirm.validation import (
    ValidationIssue,
    ValidationResult,
    SimulationMode,
    validate_ruleset,
    validate_simulation_readiness
)


# --------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------

@pytest.fixture
def ruleset():

    evaluation = EvaluationRules(
        profit_target=1500,
        drawdown=DrawdownRules(
            DrawdownType.TRAILING_EOD,
            1000
        ),
        fees=FeeRules(purchase=100),
        minimum_trading_days=3
    )

    funded = FundedRules(
        drawdown=DrawdownRules(
            DrawdownType.TRAILING_EOD,
            1000
        ),
        fees=FeeRules(activation=50),
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=1000
        ),
        live_transition=LiveTransitionRules(
            minimum_payout_count=5
        )
    )

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
        starting_balance=25_000
    )

    return Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation,
        funded=funded,
        live=live,
        max_accounts=20
    )


def error_paths(result):

    return {
        error.path
        for error in result.errors
    }


def warning_paths(result):

    return {
        warning.path
        for warning in result.warnings
    }


# --------------------------------------------------
# Validation result objects
# --------------------------------------------------

def test_validation_issue_string():

    issue = ValidationIssue(
        "evaluation.profit_target",
        "Invalid target"
    )

    assert str(issue) == (
        "evaluation.profit_target: Invalid target"
    )


def test_empty_validation_result():

    result = ValidationResult()

    assert result.is_valid
    assert not result.errors
    assert not result.warnings


def test_result_add_error():

    result = ValidationResult()

    result.add_error("test", "Invalid value")

    assert not result.is_valid
    assert len(result.errors) == 1


def test_result_add_warning():

    result = ValidationResult()

    result.add_warning("test", "Unusual value")

    assert result.is_valid
    assert len(result.warnings) == 1


def test_results_are_independent():

    first = ValidationResult()
    second = ValidationResult()

    first.add_error("test", "Invalid")

    assert not first.is_valid
    assert second.is_valid


# --------------------------------------------------
# Valid configurations
# --------------------------------------------------

def test_valid_ruleset(ruleset):

    result = validate_ruleset(ruleset)

    assert result.is_valid
    assert not result.errors


def test_ruleset_validate_method(ruleset):

    assert ruleset.validate().is_valid


def test_ruleset_readiness_method(ruleset):

    assert ruleset.validate_for_simulation().is_valid


# --------------------------------------------------
# Structural validation
# --------------------------------------------------

def test_invalid_root_object():

    result = validate_ruleset({})

    assert not result.is_valid
    assert "root" in error_paths(result)


def test_invalid_account_name_type(ruleset):

    invalid = replace(
        ruleset,
        account_name=123
    )

    result = invalid.validate()

    assert not result.is_valid
    assert "account_name" in error_paths(result)


def test_invalid_account_size_type(ruleset):

    invalid = replace(
        ruleset,
        account_size="25000"
    )

    result = invalid.validate()

    assert not result.is_valid
    assert "account_size" in error_paths(result)


def test_boolean_not_integer(ruleset):

    invalid = replace(
        ruleset,
        account_size=True
    )

    assert not invalid.validate().is_valid


def test_boolean_not_float(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            profit_target=True
        )
    )

    result = invalid.validate()

    assert (
        "evaluation.profit_target"
        in error_paths(result)
    )


def test_invalid_nested_object(ruleset):

    invalid = replace(
        ruleset,
        evaluation={}
    )

    result = invalid.validate()

    assert "evaluation" in error_paths(result)


def test_invalid_enum_type(ruleset):

    drawdown = replace(
        ruleset.evaluation.drawdown,
        type="static"
    )

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            drawdown=drawdown
        )
    )

    result = invalid.validate()

    assert (
        "evaluation.drawdown.type"
        in error_paths(result)
    )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf")
    ]
)
def test_nonfinite_values(ruleset, value):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            profit_target=value
        )
    )

    result = invalid.validate()

    assert not result.is_valid

    assert (
        "evaluation.profit_target"
        in error_paths(result)
    )


# --------------------------------------------------
# Root semantic validation
# --------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["", " ", "   "]
)
def test_empty_account_name(ruleset, name):

    result = replace(
        ruleset,
        account_name=name
    ).validate()

    assert "account_name" in error_paths(result)


@pytest.mark.parametrize(
    "size",
    [0, -1000]
)
def test_invalid_account_size(ruleset, size):

    result = replace(
        ruleset,
        account_size=size
    ).validate()

    assert "account_size" in error_paths(result)


def test_invalid_max_accounts(ruleset):

    result = replace(
        ruleset,
        max_accounts=0
    ).validate()

    assert "max_accounts" in error_paths(result)


# --------------------------------------------------
# Fundamental rule validation
# --------------------------------------------------

def test_invalid_drawdown(ruleset):

    drawdown = replace(
        ruleset.evaluation.drawdown,
        amount=-1000
    )

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            drawdown=drawdown
        )
    )

    assert (
        "evaluation.drawdown.amount"
        in error_paths(invalid.validate())
    )


def test_static_drawdown_with_cap(ruleset):

    drawdown = DrawdownRules(
        type=DrawdownType.STATIC,
        amount=1000,
        trailing_stop_balance=25000
    )

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            drawdown=drawdown
        )
    )

    assert (
        "evaluation.drawdown.trailing_stop_balance"
        in error_paths(invalid.validate())
    )


def test_negative_fee(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            fees=FeeRules(purchase=-100)
        )
    )

    assert (
        "evaluation.fees.purchase"
        in error_paths(invalid.validate())
    )


def test_missing_recurring_interval(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            fees=FeeRules(recurring=100)
        )
    )

    assert (
        "evaluation.fees.recurring_interval_days"
        in error_paths(invalid.validate())
    )


def test_invalid_recurring_interval(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            fees=FeeRules(
                recurring=100,
                recurring_interval_days=0
            )
        )
    )

    assert (
        "evaluation.fees.recurring_interval_days"
        in error_paths(invalid.validate())
    )


def test_invalid_daily_loss(ruleset):

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            daily_loss=DailyLossRules(-500)
        )
    )

    assert (
        "funded.daily_loss.amount"
        in error_paths(invalid.validate())
    )


@pytest.mark.parametrize(
    "ratio",
    [-0.5, 0, 1.5]
)
def test_invalid_consistency(ruleset, ratio):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            consistency=ConsistencyRules(ratio)
        )
    )

    assert (
        "evaluation.consistency.maximum_ratio"
        in error_paths(invalid.validate())
    )


def test_invalid_contract_limit(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            trading=TradingRules(
                max_micro_contracts=-1
            )
        )
    )

    assert (
        "evaluation.trading.max_micro_contracts"
        in error_paths(invalid.validate())
    )


def test_zero_contract_warning(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            trading=TradingRules(
                max_mini_contracts=0,
                max_micro_contracts=0
            )
        )
    )

    result = invalid.validate()

    assert result.is_valid

    assert (
        "evaluation.trading"
        in warning_paths(result)
    )


# --------------------------------------------------
# Payout validation
# --------------------------------------------------

@pytest.mark.parametrize(
    "split",
    [-0.1, 1.1]
)
def test_invalid_profit_split(ruleset, split):

    payout = replace(
        ruleset.funded.payout,
        profit_split=split
    )

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            payout=payout
        )
    )

    assert (
        "funded.payout.profit_split"
        in error_paths(invalid.validate())
    )


def test_invalid_payout_range(ruleset):

    payout = replace(
        ruleset.funded.payout,
        minimum_amount=2000,
        maximum_amount=1000
    )

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            payout=payout
        )
    )

    assert (
        "funded.payout.maximum_amount"
        in error_paths(invalid.validate())
    )


def test_invalid_lifetime_payout_limit(ruleset):

    payout = replace(
        ruleset.funded.payout,
        maximum_total_payouts=100
    )

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            payout=payout
        )
    )

    assert (
        "funded.payout.maximum_total_payouts"
        in error_paths(invalid.validate())
    )


def test_invalid_withdrawal_ratio(ruleset):

    payout = replace(
        ruleset.funded.payout,
        maximum_withdrawal_ratio=1.5
    )

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            payout=payout
        )
    )

    assert (
        "funded.payout.maximum_withdrawal_ratio"
        in error_paths(invalid.validate())
    )


def test_nested_payout_consistency(ruleset):

    payout = replace(
        ruleset.funded.payout,
        consistency=ConsistencyRules(2.0)
    )

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            payout=payout
        )
    )

    assert (
        "funded.payout.consistency.maximum_ratio"
        in error_paths(invalid.validate())
    )


# --------------------------------------------------
# Evaluation validation
# --------------------------------------------------

def test_invalid_profit_target(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            profit_target=0
        )
    )

    assert (
        "evaluation.profit_target"
        in error_paths(invalid.validate())
    )


def test_invalid_trading_day_range(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            minimum_trading_days=10,
            maximum_trading_days=5
        )
    )

    assert (
        "evaluation.maximum_trading_days"
        in error_paths(invalid.validate())
    )


def test_invalid_calendar_day_range(ruleset):

    invalid = replace(
        ruleset,
        evaluation=replace(
            ruleset.evaluation,
            minimum_trading_days=10,
            maximum_calendar_days=5
        )
    )

    assert (
        "evaluation.maximum_calendar_days"
        in error_paths(invalid.validate())
    )


# --------------------------------------------------
# Transition validation
# --------------------------------------------------

def test_empty_automatic_transition(ruleset):

    invalid = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            live_transition=LiveTransitionRules()
        )
    )

    assert (
        "funded.live_transition"
        in error_paths(invalid.validate())
    )


def test_manual_transition_warning(ruleset):

    config = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            live_transition=LiveTransitionRules(
                transition_type=TransitionType.MANUAL_REVIEW
            )
        )
    )

    result = config.validate()

    assert result.is_valid

    assert (
        "funded.live_transition"
        in warning_paths(result)
    )


# --------------------------------------------------
# Simulation readiness
# --------------------------------------------------

def test_complete_lifecycle_ready(ruleset):

    result = validate_simulation_readiness(
        ruleset
    )

    assert result.is_valid


def test_empty_ruleset_not_ready():

    config = Ruleset("Empty", 25_000)

    assert config.validate().is_valid

    result = config.validate_for_simulation()

    assert not result.is_valid
    assert "stages" in error_paths(result)


def test_evaluation_only_ready(ruleset):

    config = replace(
        ruleset,
        funded=None,
        live=None
    )

    result = config.validate_for_simulation(
        SimulationMode.EVALUATION_ONLY
    )

    assert result.is_valid


def test_evaluation_only_not_full_ready(ruleset):

    config = replace(
        ruleset,
        funded=None,
        live=None
    )

    result = config.validate_for_simulation()

    assert "funded" in error_paths(result)


def test_missing_evaluation(ruleset):

    config = replace(
        ruleset,
        evaluation=None
    )

    result = config.validate_for_simulation(
        SimulationMode.EVALUATION_ONLY
    )

    assert "evaluation" in error_paths(result)


def test_missing_funded(ruleset):

    config = replace(
        ruleset,
        funded=None
    )

    result = config.validate_for_simulation(
        SimulationMode.FUNDED_ONLY
    )

    assert "funded" in error_paths(result)


def test_funded_only_ready(ruleset):

    config = replace(
        ruleset,
        evaluation=None,
        live=None
    )

    result = config.validate_for_simulation(
        SimulationMode.FUNDED_ONLY
    )

    assert result.is_valid


def test_missing_live_configuration(ruleset):

    config = replace(
        ruleset,
        live=None
    )

    result = config.validate_for_simulation()

    assert "live" in error_paths(result)


def test_missing_live_transition(ruleset):

    config = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            live_transition=None
        )
    )

    result = config.validate_for_simulation()

    assert (
        "funded.live_transition"
        in error_paths(result)
    )


def test_unresolved_live_balance(ruleset):

    config = replace(
        ruleset,
        live=replace(
            ruleset.live,
            starting_balance=None
        )
    )

    result = config.validate_for_simulation()

    assert (
        "live.starting_balance"
        in error_paths(result)
    )


def test_manual_review_not_ready(ruleset):

    config = replace(
        ruleset,
        funded=replace(
            ruleset.funded,
            live_transition=LiveTransitionRules(
                transition_type=TransitionType.MANUAL_REVIEW
            )
        )
    )

    result = config.validate_for_simulation()

    assert not result.is_valid

    assert (
        "funded.live_transition"
        in error_paths(result)
    )


def test_invalid_simulation_mode(ruleset):

    result = ruleset.validate_for_simulation(
        "unknown"
    )

    assert "simulation.mode" in error_paths(result)


# --------------------------------------------------
# Multiple errors
# --------------------------------------------------

def test_collect_multiple_errors(ruleset):

    invalid = replace(
        ruleset,
        account_name="",
        account_size=-1000,
        max_accounts=-1
    )

    result = invalid.validate()

    assert len(result.errors) == 3

    assert error_paths(result) == {
        "account_name",
        "account_size",
        "max_accounts"
    }


def test_collect_nested_errors(ruleset):

    invalid = replace(
        ruleset,

        evaluation=replace(
            ruleset.evaluation,
            profit_target=-1000
        ),

        funded=replace(
            ruleset.funded,
            payout=replace(
                ruleset.funded.payout,
                profit_split=1.5
            )
        )
    )

    result = invalid.validate()

    assert error_paths(result) == {
        "evaluation.profit_target",
        "funded.payout.profit_split"
    }


def test_structural_error_prevents_typeerror(ruleset):

    invalid = replace(
        ruleset,
        account_size="invalid",
        max_accounts=-1
    )

    result = invalid.validate()

    assert not result.is_valid
    assert "account_size" in error_paths(result)


def test_validation_does_not_modify_ruleset(ruleset):

    original_size = ruleset.account_size
    original_target = ruleset.evaluation.profit_target

    ruleset.validate()
    ruleset.validate_for_simulation()

    assert ruleset.account_size == original_size

    assert (
        ruleset.evaluation.profit_target
        == original_target
    )