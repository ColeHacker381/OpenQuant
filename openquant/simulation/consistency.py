from dataclasses import dataclass
from fractions import Fraction
from decimal import Decimal

from ..propfirm.ruleset import ConsistencyRules


class ConsistencyConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ConsistencyResult:

    total_profit_cents: int

    best_day_cents: int

    maximum_ratio: Fraction

    @property
    def actual_ratio(self) -> Fraction | None:

        if self.total_profit_cents <= 0:
            return None

        return Fraction(
            self.best_day_cents,
            self.total_profit_cents
        )

    @property
    def satisfied(self) -> bool:

        if self.total_profit_cents <= 0:
            return False

        return (
            self.best_day_cents
            * self.maximum_ratio.denominator
            <=
            self.total_profit_cents
            * self.maximum_ratio.numerator
        )

    @property
    def required_profit_cents(self) -> int:

        numerator = (
            self.best_day_cents
            * self.maximum_ratio.denominator
        )

        denominator = (
            self.maximum_ratio.numerator
        )

        # Integer ceiling division.
        return (
            numerator + denominator - 1
        ) // denominator

    @property
    def additional_profit_cents(self) -> int:

        required = max(
            1,
            self.required_profit_cents
        )

        return max(
            0,
            required - self.total_profit_cents
        )


def compile_consistency(
    rules: ConsistencyRules
) -> Fraction:

    if not isinstance(rules, ConsistencyRules):

        raise ConsistencyConfigurationError(
            "Expected ConsistencyRules"
        )

    value = rules.maximum_ratio

    if type(value) not in (int, float):

        raise ConsistencyConfigurationError(
            "Consistency ratio must be numeric"
        )

    decimal = Decimal(str(value))

    if (
        not decimal.is_finite()
        or not 0 < decimal <= 1
    ):

        raise ConsistencyConfigurationError(
            "Consistency ratio must be in (0, 1]"
        )

    return Fraction(decimal)


def evaluate_consistency(
    total_profit_cents: int,
    best_day_cents: int,
    maximum_ratio: Fraction
) -> ConsistencyResult:

    if type(total_profit_cents) is not int:

        raise TypeError(
            "Total profit must be integer cents"
        )

    if (
        type(best_day_cents) is not int
        or best_day_cents < 0
    ):

        raise ValueError(
            "Best day must be nonnegative integer cents"
        )

    if (
        not isinstance(maximum_ratio, Fraction)
        or not 0 < maximum_ratio <= 1
    ):

        raise ConsistencyConfigurationError(
            "Invalid maximum consistency ratio"
        )

    return ConsistencyResult(
        total_profit_cents=total_profit_cents,
        best_day_cents=best_day_cents,
        maximum_ratio=maximum_ratio
    )