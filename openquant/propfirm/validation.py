from dataclasses import dataclass, field, fields, is_dataclass
import math
from types import UnionType
from typing import (
    Union,
    get_args,
    get_origin,
    get_type_hints
)

from .ruleset import (
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

from enum import Enum

class SimulationMode(str, Enum):
    EVALUATION_ONLY = "evaluation_only"
    FUNDED_ONLY = "funded_only"
    FULL_LIFECYCLE = "full_lifecycle"

@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"

@dataclass(frozen=True)
class ValidationResult:
    errors: list[ValidationIssue] = field(
        default_factory=list
    )

    warnings: list[ValidationIssue] = field(
        default_factory=list
    )

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, path: str, message: str):
        self.errors.append(
            ValidationIssue(path, message)
        )

    def add_warning(self, path: str, message: str):
        self.warnings.append(
            ValidationIssue(path, message)
        )

    def __str__(self) -> str:
        lines = [
            "Configuration valid"
            if self.is_valid
            else "Configuration invalid"
        ]

        for error in self.errors:
            lines.append(f"ERROR: {error}")

        for warning in self.warnings:
            lines.append(f"WARNING: {warning}")

        return "\n".join(lines)

def matches_type(value, expected_type) -> bool:

    origin = get_origin(expected_type)

    #handle unions such as float | None
    if origin in (Union, UnionType):
        return any(
            matches_type(value, option)
            for option in get_args(expected_type)
        )

    if expected_type is type(None):
        return value is None

    if expected_type is float:
        return type(value) in (int, float)

    if expected_type is int:
        return type(value) is int

    if expected_type is bool:
        return type(value) is bool

    return isinstance(value, expected_type)

def validate_structure(
    obj,
    result: ValidationResult,
    path: str = ""
) -> None:

    if not is_dataclass(obj) or isinstance(obj, type):
        result.add_error(
            path or "root",
            "Expected a dataclass instance"
        )
        return

    hints = get_type_hints(type(obj))
    for config_field in fields(obj):

        name = config_field.name
        value = getattr(obj, name)
        expected_type = hints[name]
        field_path = f"{path}.{name}" if path else name

        if not matches_type(value, expected_type):
            result.add_error(
                field_path,
                (
                    f"Expected {expected_type}"
                    f"recieved {type(value).__name__}"
                )
            )

            continue

        if value is None:
            continue

        if type(value) is float:
            if not math.isfinite(value):
                result.add_error(
                    field_path,
                    "Must be a finite number"
                )

                continue

        #recursively check value if dataclass
        if is_dataclass(value):
            validate_structure(
                value,
                result,
                field_path
            )

def check_positive(
    value,
    path: str,
    result: ValidationResult
) -> None:

    if value is not None and value <= 0:
        result.add_error(
            path,
            "Must be greater than zero"
        )

def check_nonnegative(
    value,
    path: str,
    result: ValidationResult
) -> None:

    if value is not None and value < 0:
        result.add_error(
            path,
            "Must be greater than or equal to zero"
        )

def check_ratio(
    value,
    path: str,
    result: ValidationResult,
    allow_zero: bool = False
) -> None:

    if value is None:
        return

    minimum_valid = (
        value >= 0
        if allow_zero
        else value > 0
    )

    if not minimum_valid or value > 1:
        result.add_error(
            path,
            (
                "Must be between 0 and 1 inclusive"
                if allow_zero
                else "Must be greater than 0 and at most 1"
            )
        )

def check_range(
    minimum,
    maximum,
    path: str,
    result: ValidationResult
) -> None:
    
    if minimum is None or maximum is None:
        return
    if minimum > maximum:
        result.add_error(
            path,
            "Maximum cannot be less than minimum"
        )

def validate_drawdown(
    rules: DrawdownRules,
    path: str,
    result: ValidationResult
) -> None:
    check_positive(
        rules.amount,
        f"{path}.amount",
        result
    )

    if rules.type is DrawdownType.STATIC:
        if rules.trailing_stop_balance is not None:
            result.add_error(
                f"{path}.trailing_stop_balance",
                "Static drawdown cannot have a trailing cap"
            )

def validate_fees(
    rules: FeeRules,
    path: str,
    result: ValidationResult
) -> None:

    for name in (
        "purchase",
        "activation",
        "recurring",
        "reset"
    ):
        check_nonnegative(
            getattr(rules, name),
            f"{path}.{name}",
            result
        )

    check_positive(
        rules.recurring_interval_days,
        f"{path}.recurring_interval_days",
        result
    )

    if rules.recurring > 0:
        if rules.recurring_interval_days is None:
            result.add_error(
                f"{path}.recurring_interval_days",
                "Required when recurring fees are enabled"
            )

def validate_daily_loss(
        rules: DailyLossRules,
        path: str,
        result: ValidationResult
) -> None:

    check_positive(
        rules.amount,
        f"{path}.amount",
        result
    )

def validate_consistency(
    rules: ConsistencyRules,
    path: str,
    result: ValidationResult
) -> None:

    check_ratio(
        rules.maximum_ratio,
        f"{path}.maximum_ratio",
        result
    )

def validate_trading(
        rules: TradingRules,
        path: str,
        result: ValidationResult
) -> None:

    check_nonnegative(
        rules.max_mini_contracts,
        f"{path}.max_mini_contracts",
        result
    )

    check_nonnegative(
        rules.max_micro_contracts,
        f"{path}.max_micro_contracts",
        result
    )

    if (
        rules.max_mini_contracts == 0
        and rules.max_micro_contracts == 0
    ):
        result.add_warning(
            path,
            "Both contract limits are zero"
        )

def validate_payout(
    rules: PayoutRules,
    path: str,
    result: ValidationResult
) -> None:

    check_ratio(
        rules.profit_split,
        f"{path}.profit_split",
        result,
        allow_zero=True
    )

    check_positive(
        rules.minimum_amount,
        f"{path}.minimum_amount",
        result
    )

    check_positive(
        rules.maximum_amount,
        f"{path}.maximum_amount",
        result
    )

    check_range(
        rules.minimum_amount,
        rules.maximum_amount,
        f"{path}.maximum_amount",
        result
    )

    for name in (
        "minimum_profitable_days",
        "profitable_day_threshold",
        "minimum_trading_days",
        "minimum_days_between_payouts"
    ):

        check_nonnegative(
            getattr(rules, name),
            f"{path}.{name}",
            result
        )

    check_ratio(
        rules.maximum_withdrawal_ratio,
        f"{path}.maximum_withdrawal_ratio",
        result
    )

    check_positive(
        rules.maximum_total_payouts,
        f"{path}.maximum_total_payouts",
        result
    )

    check_positive(
        rules.maximum_payout_count,
        f"{path}.maximum_payout_count",
        result
    )

    check_range(
        rules.minimum_amount,
        rules.maximum_total_payouts,
        f"{path}.maximum_total_payouts",
        result
    )

    if rules.consistency is not None:

        validate_consistency(
            rules.consistency,
            f"{path}.consistency",
            result
        )

    if rules.profit_split == 0:

        result.add_warning(
            f"{path}.profit_split",
            "Trader receives no payout revenue"
        )

def validate_transition(
    rules: LiveTransitionRules,
    path: str,
    result: ValidationResult
) -> None:

    requirements = (
        "minimum_payout_count",
        "minimum_total_payouts",
        "minimum_trading_days",
        "minimum_profit"
    )

    for name in requirements:
        check_positive(
            getattr(rules, name),
            f"{path}.{name}",
            result
        )

    if rules.transition_type is TransitionType.AUTOMATIC:
        if all(
            getattr(rules, name) is None
            for name in requirements
        ):
            result.add_error(
                path,
                "Automatic transition requires at least one condition"
            )

    if rules.transition_type is TransitionType.MANUAL_REVIEW:
        result.add_warning(
            path,
            "Live transition depends on discretionary review"
        )

def validate_common_stage(
    rules: EvaluationRules | FundedRules | LiveRules,
    path: str,
    result: ValidationResult
) -> None:

    if rules.drawdown is not None:

        validate_drawdown(
            rules.drawdown,
            f"{path}.drawdown",
            result
        )

    validate_fees(
        rules.fees,
        f"{path}.fees",
        result
    )

    if rules.daily_loss is not None:

        validate_daily_loss(
            rules.daily_loss,
            f"{path}.daily_loss",
            result
        )

    if rules.consistency is not None:

        validate_consistency(
            rules.consistency,
            f"{path}.consistency",
            result
        )

    if rules.trading is not None:

        validate_trading(
            rules.trading,
            f"{path}.trading",
            result
        )

def validate_evaluation(
    rules: EvaluationRules,
    path: str,
    result: ValidationResult
) -> None:

    validate_common_stage(
        rules,
        path,
        result
    )

    check_positive(
        rules.profit_target,
        f"{path}.profit_target",
        result
    )

    check_nonnegative(
        rules.minimum_trading_days,
        f"{path}.minimum_trading_days",
        result
    )

    check_positive(
        rules.maximum_trading_days,
        f"{path}.maximum_trading_days",
        result
    )

    check_positive(
        rules.maximum_calendar_days,
        f"{path}.maximum_calendar_days",
        result
    )

    check_range(
        rules.minimum_trading_days,
        rules.maximum_trading_days,
        f"{path}.maximum_trading_days",
        result
    )

    check_range(
        rules.minimum_trading_days,
        rules.maximum_calendar_days,
        f"{path}.maximum_calendar_days",
        result
    )

def validate_funded(
    rules: FundedRules,
    path: str,
    result: ValidationResult
) -> None:

    validate_common_stage(
        rules,
        path,
        result
    )

    validate_payout(
        rules.payout,
        f"{path}.payout",
        result
    )

    if rules.live_transition is not None:

        validate_transition(
            rules.live_transition,
            f"{path}.live_transition",
            result
        )


def validate_live(
    rules: LiveRules,
    path: str,
    result: ValidationResult
) -> None:

    validate_common_stage(
        rules,
        path,
        result
    )

    validate_payout(
        rules.payout,
        f"{path}.payout",
        result
    )

    check_positive(
        rules.starting_balance,
        f"{path}.starting_balance",
        result
    )


def validate_ruleset(
    ruleset: Ruleset
) -> ValidationResult:

    result = ValidationResult()
    if not isinstance(ruleset, Ruleset):

        result.add_error(
            "root",
            "Expected a Ruleset instance"
        )

        return result

    # Level 1: structural validation
    validate_structure(ruleset, result)
    if not result.is_valid:
        return result

    # Level 2: semantic validation

    if not ruleset.account_name.strip():
        result.add_error(
            "account_name",
            "Account name cannot be empty"
        )

    check_positive(
        ruleset.account_size,
        "account_size",
        result
    )

    check_positive(
        ruleset.max_accounts,
        "max_accounts",
        result
    )

    if ruleset.evaluation is not None:

        validate_evaluation(
            ruleset.evaluation,
            "evaluation",
            result
        )

    if ruleset.funded is not None:

        validate_funded(
            ruleset.funded,
            "funded",
            result
        )

    if ruleset.live is not None:

        validate_live(
            ruleset.live,
            "live",
            result
        )

    return result

def validate_simulation_readiness(
    ruleset: Ruleset,
    mode: SimulationMode = SimulationMode.FULL_LIFECYCLE
) -> ValidationResult:

    result = validate_ruleset(ruleset)

    if not result.is_valid:
        return result

    if not isinstance(mode, SimulationMode):

        result.add_error(
            "simulation.mode",
            "Invalid simulation mode"
        )

        return result

    if mode is SimulationMode.EVALUATION_ONLY:

        if ruleset.evaluation is None:

            result.add_error(
                "evaluation",
                "Evaluation configuration is required"
            )

    elif mode is SimulationMode.FUNDED_ONLY:

        if ruleset.funded is None:

            result.add_error(
                "funded",
                "Funded configuration is required"
            )

    elif mode is SimulationMode.FULL_LIFECYCLE:

        if (
            ruleset.evaluation is None
            and ruleset.funded is None
            and ruleset.live is None
        ):

            result.add_error(
                "stages",
                "At least one account stage is required"
            )

        if (
            ruleset.evaluation is not None
            and ruleset.funded is None
        ):

            result.add_error(
                "funded",
                "Funded configuration is required after evaluation"
            )

        if ruleset.funded is not None:

            transition = ruleset.funded.live_transition

            if transition is not None:

                if ruleset.live is None:

                    result.add_error(
                        "live",
                        "Live configuration is required for transition"
                    )

                if (
                    transition.transition_type
                    is TransitionType.MANUAL_REVIEW
                ):

                    result.add_error(
                        "funded.live_transition",
                        "Manual review requires a simulation outcome model"
                    )

            elif ruleset.live is not None:

                result.add_error(
                    "funded.live_transition",
                    "Transition conditions are required to reach live"
                )

        if ruleset.live is not None:

            if ruleset.live.starting_balance is None:

                result.add_error(
                    "live.starting_balance",
                    "Initial live balance must be configured"
                )

    return result


