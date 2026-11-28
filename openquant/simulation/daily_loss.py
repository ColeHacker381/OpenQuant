from dataclasses import dataclass
from datetime import datetime

from ..propfirm.ruleset import (
    DailyLossRules,
    BalanceReference,
    BreachAction
)

from .account import AccountState

from .enums import (
    AccountStatus,
    TerminationReason
)

from .events import AccountEvent

from .drawdown import get_reference_value

from .money import dollars_to_cents


class DailyLossConfigurationError(ValueError):
    pass


class DailyLossInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledDailyLoss:

    amount_cents: int

    reference: BalanceReference

    action: BreachAction

    breach_on_equal: bool

def compile_daily_loss(
    rules: DailyLossRules
) -> CompiledDailyLoss:

    if not isinstance(rules, DailyLossRules):

        raise DailyLossConfigurationError(
            "Expected DailyLossRules"
        )

    try:

        amount = dollars_to_cents(
            rules.amount
        )

    except (TypeError, ValueError) as exc:

        raise DailyLossConfigurationError(
            str(exc)
        ) from exc

    if amount <= 0:

        raise DailyLossConfigurationError(
            "Daily loss amount must be positive"
        )

    if not isinstance(
        rules.reference,
        BalanceReference
    ):

        raise DailyLossConfigurationError(
            "Invalid daily loss reference"
        )

    if not isinstance(
        rules.action,
        BreachAction
    ):

        raise DailyLossConfigurationError(
            "Invalid daily loss action"
        )

    if type(rules.breach_on_equal) is not bool:

        raise DailyLossConfigurationError(
            "Invalid daily loss boundary configuration"
        )

    return CompiledDailyLoss(
        amount_cents=amount,
        reference=rules.reference,
        action=rules.action,
        breach_on_equal=rules.breach_on_equal
    )

def get_session_start_reference(
    account: AccountState,
    rules: CompiledDailyLoss
) -> int:

    if not account.has_active_session:

        raise DailyLossInvariantError(
            "Daily loss requires an active session"
        )

    if rules.reference is BalanceReference.BALANCE:

        value = account.session_start_balance_cents

    elif rules.reference is BalanceReference.EQUITY:

        value = account.session_start_equity_cents

    else:

        raise DailyLossConfigurationError(
            "Unsupported balance reference"
        )

    if type(value) is not int:

        raise DailyLossInvariantError(
            "Invalid session starting reference"
        )

    return value

def calculate_daily_loss_floor(
    account: AccountState,
    rules: CompiledDailyLoss
) -> int:

    starting_value = get_session_start_reference(
        account,
        rules
    )

    return starting_value - rules.amount_cents

def calculate_daily_loss(
    account: AccountState,
    rules: CompiledDailyLoss
) -> int:

    starting_value = get_session_start_reference(
        account,
        rules
    )

    current_value = get_reference_value(
        account,
        rules.reference
    )

    return max(
        0,
        starting_value - current_value
    )

def is_daily_loss_breached(
    account: AccountState,
    rules: CompiledDailyLoss
) -> bool:

    if not account.has_active_session:

        return False

    floor = calculate_daily_loss_floor(
        account,
        rules
    )

    current_value = get_reference_value(
        account,
        rules.reference
    )

    if rules.breach_on_equal:

        return current_value <= floor

    return current_value < floor

@dataclass(frozen=True)
class DailyLossBreach:

    timestamp: datetime

    session_id: str

    starting_value_cents: int

    monitored_value_cents: int

    floor_cents: int

    action: BreachAction

    @property
    def loss_cents(self) -> int:

        return max(
            0,
            self.starting_value_cents
            - self.monitored_value_cents
        )

    @property
    def overshoot_cents(self) -> int:

        return max(
            0,
            self.floor_cents
            - self.monitored_value_cents
        )

def process_daily_loss(
    account: AccountState,
    rules: CompiledDailyLoss,
    event: AccountEvent
) -> DailyLossBreach | None:

    if not account.is_active:

        return None

    if not account.has_active_session:

        return None

    if account.session_locked:

        return None

    if not is_daily_loss_breached(
        account,
        rules
    ):

        return None

    breach = DailyLossBreach(
        timestamp=event.timestamp,

        session_id=account.session_id,

        starting_value_cents=(
            get_session_start_reference(
                account,
                rules
            )
        ),

        monitored_value_cents=(
            get_reference_value(
                account,
                rules.reference
            )
        ),

        floor_cents=(
            calculate_daily_loss_floor(
                account,
                rules
            )
        ),

        action=rules.action
    )

    if rules.action is BreachAction.TERMINATE:

        account.status = AccountStatus.TERMINATED

        account.termination_reason = (
            TerminationReason.DAILY_LOSS
        )

    elif rules.action is BreachAction.LOCK_SESSION:

        account.session_locked = True

    else:

        raise DailyLossConfigurationError(
            "Unsupported daily loss action"
        )

    return breach

