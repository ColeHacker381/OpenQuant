from dataclasses import dataclass
from datetime import datetime

from ..propfirm.ruleset import (
    DrawdownRules,
    DrawdownType,
    BalanceReference
)

from .account import AccountState

from .events import (
    AccountEvent,
    SessionEnded
)

from .enums import (
    AccountStatus,
    TerminationReason
)

from .money import dollars_to_cents


class DrawdownConfigurationError(ValueError):
    pass


class DrawdownInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledDrawdown:

    type: DrawdownType

    amount_cents: int

    trailing_cap_cents: int | None

    reference: BalanceReference

    breach_reference: BalanceReference

    breach_on_equal: bool

def compile_drawdown(
    rules: DrawdownRules
) -> CompiledDrawdown:

    if not isinstance(rules, DrawdownRules):

        raise DrawdownConfigurationError(
            "Expected DrawdownRules"
        )

    try:

        amount = dollars_to_cents(
            rules.amount
        )

        cap = (
            None
            if rules.trailing_stop_balance is None
            else dollars_to_cents(
                rules.trailing_stop_balance
            )
        )

    except (ValueError, TypeError) as exc:

        raise DrawdownConfigurationError(
            str(exc)
        ) from exc

    if amount <= 0:

        raise DrawdownConfigurationError(
            "Drawdown amount must be positive"
        )

    if not isinstance(rules.type, DrawdownType):

        raise DrawdownConfigurationError(
            "Invalid drawdown type"
        )

    if not isinstance(
        rules.reference,
        BalanceReference
    ):

        raise DrawdownConfigurationError(
            "Invalid high-water mark reference"
        )

    if not isinstance(
        rules.breach_reference,
        BalanceReference
    ):

        raise DrawdownConfigurationError(
            "Invalid breach reference"
        )

    if type(rules.breach_on_equal) is not bool:

        raise DrawdownConfigurationError(
            "Invalid breach boundary configuration"
        )

    if (
        rules.type is DrawdownType.STATIC
        and cap is not None
    ):

        raise DrawdownConfigurationError(
            "Static drawdown cannot have a trailing cap"
        )

    return CompiledDrawdown(
        type=rules.type,
        amount_cents=amount,
        trailing_cap_cents=cap,
        reference=rules.reference,
        breach_reference=rules.breach_reference,
        breach_on_equal=rules.breach_on_equal
    )

def get_reference_value(
    account: AccountState,
    reference: BalanceReference
) -> int:

    if reference is BalanceReference.BALANCE:

        return account.balance_cents

    if reference is BalanceReference.EQUITY:

        return account.equity_cents

    raise DrawdownConfigurationError(
        f"Unsupported balance reference: {reference}"
    )

def calculate_floor(
    high_water_mark_cents: int,
    rules: CompiledDrawdown
) -> int:

    floor = (
        high_water_mark_cents
        - rules.amount_cents
    )

    if rules.trailing_cap_cents is not None:

        floor = min(
            floor,
            rules.trailing_cap_cents
        )

    return floor

def initialize_drawdown(
    account: AccountState,
    rules: CompiledDrawdown
) -> None:

    if (
        account.high_water_mark_cents is not None
        or account.drawdown_floor_cents is not None
    ):

        raise DrawdownInvariantError(
            "Drawdown state is already initialized"
        )

    starting_balance = (
        account.starting_balance_cents
    )

    initial_floor = (
        starting_balance
        - rules.amount_cents
    )

    if (
        rules.trailing_cap_cents is not None
        and rules.trailing_cap_cents < initial_floor
    ):

        raise DrawdownConfigurationError(
            "Trailing cap cannot be below the "
            "initial drawdown floor"
        )

    account.high_water_mark_cents = (
        starting_balance
    )

    account.drawdown_floor_cents = initial_floor

def should_update_high_water_mark(
    rules: CompiledDrawdown,
    event: AccountEvent
) -> bool:

    if rules.type is DrawdownType.STATIC:

        return False

    if rules.type is DrawdownType.TRAILING_EOD:

        return isinstance(
            event,
            SessionEnded
        )

    if rules.type is DrawdownType.TRAILING_INTRADAY:

        return True

    raise DrawdownConfigurationError(
        "Unsupported drawdown type"
    )

def update_high_water_mark(
    account: AccountState,
    rules: CompiledDrawdown,
    event: AccountEvent
) -> None:

    if not should_update_high_water_mark(
        rules,
        event
    ):

        return

    if (
        account.high_water_mark_cents is None
        or account.drawdown_floor_cents is None
    ):

        raise DrawdownInvariantError(
            "Drawdown state is not initialized"
        )

    reference_value = get_reference_value(
        account,
        rules.reference
    )

    new_high = max(
        account.high_water_mark_cents,
        reference_value
    )

    new_floor = calculate_floor(
        new_high,
        rules
    )

    account.high_water_mark_cents = new_high

    account.drawdown_floor_cents = max(
        account.drawdown_floor_cents,
        new_floor
    )

def is_drawdown_breached(
    account: AccountState,
    rules: CompiledDrawdown
) -> bool:

    if account.drawdown_floor_cents is None:

        raise DrawdownInvariantError(
            "Drawdown floor is not initialized"
        )

    value = get_reference_value(
        account,
        rules.breach_reference
    )

    floor = account.drawdown_floor_cents

    if rules.breach_on_equal:

        return value <= floor

    return value < floor

@dataclass(frozen=True)
class DrawdownBreach:

    timestamp: datetime

    monitored_value_cents: int

    floor_cents: int

    high_water_mark_cents: int

    @property
    def overshoot_cents(self) -> int:

        return max(
            0,
            self.floor_cents
            - self.monitored_value_cents
        )

def terminate_for_drawdown(
    account: AccountState,
    rules: CompiledDrawdown,
    event: AccountEvent
) -> DrawdownBreach:

    breach = DrawdownBreach(
        timestamp=event.timestamp,

        monitored_value_cents=get_reference_value(
            account,
            rules.breach_reference
        ),

        floor_cents=account.drawdown_floor_cents,

        high_water_mark_cents=(
            account.high_water_mark_cents
        )
    )

    account.status = AccountStatus.TERMINATED

    account.termination_reason = (
        TerminationReason.DRAWDOWN
    )

    return breach

def process_drawdown(
    account: AccountState,
    rules: CompiledDrawdown,
    event: AccountEvent
) -> DrawdownBreach | None:

    if account.status is not AccountStatus.ACTIVE:

        return None

    if is_drawdown_breached(
        account,
        rules
    ):

        return terminate_for_drawdown(
            account,
            rules,
            event
        )

    update_high_water_mark(
        account,
        rules,
        event
    )

    if is_drawdown_breached(
        account,
        rules
    ):

        return terminate_for_drawdown(
            account,
            rules,
            event
        )

    return None

def assert_drawdown_invariants(
    account: AccountState,
    rules: CompiledDrawdown
) -> None:

    high = account.high_water_mark_cents

    floor = account.drawdown_floor_cents

    if type(high) is not int:

        raise DrawdownInvariantError(
            "High-water mark must be integer cents"
        )

    if type(floor) is not int:

        raise DrawdownInvariantError(
            "Drawdown floor must be integer cents"
        )

    if high < account.starting_balance_cents:

        raise DrawdownInvariantError(
            "High-water mark is below starting balance"
        )

    if rules.type is DrawdownType.STATIC:

        if high != account.starting_balance_cents:

            raise DrawdownInvariantError(
                "Static drawdown high-water mark changed"
            )

    expected_floor = calculate_floor(
        high,
        rules
    )

    if floor != expected_floor:

        raise DrawdownInvariantError(
            f"Drawdown floor mismatch: "
            f"expected {expected_floor}, "
            f"found {floor}"
        )

