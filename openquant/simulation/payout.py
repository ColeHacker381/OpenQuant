from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from fractions import Fraction
from decimal import Decimal

from ..propfirm.ruleset import (
    PayoutRules,
    ConsistencyRules,
    ConsistencyWindow
)

from .account import (
    AccountState,
    CashFlow,
    PayoutRecord
)

from .enums import (
    AccountStage,
    CashFlowType
)

from .events import PayoutRequested

from .money import dollars_to_cents

from .consistency import (
    compile_consistency,
    evaluate_consistency
)


class PayoutConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPayout:

    profit_split: Fraction

    minimum_amount_cents: int

    maximum_amount_cents: int | None

    minimum_profitable_days: int

    profitable_day_threshold_cents: int

    minimum_trading_days: int

    minimum_balance_after_payout_cents: int | None

    minimum_days_between_payouts: int

    maximum_withdrawal_ratio: Fraction | None

    consistency: ConsistencyRules | None

    maximum_total_payouts_cents: int | None

    maximum_payout_count: int | None


def optional_cents(value) -> int | None:

    if value is None:
        return None

    return dollars_to_cents(value)


def compile_ratio(
    value,
    *,
    allow_zero=False
) -> Fraction:

    if type(value) not in (int, float):

        raise PayoutConfigurationError(
            "Ratio must be numeric"
        )

    decimal = Decimal(str(value))

    if not decimal.is_finite():

        raise PayoutConfigurationError(
            "Ratio must be finite"
        )

    if (
        decimal < 0
        or decimal > 1
        or (decimal == 0 and not allow_zero)
    ):

        raise PayoutConfigurationError(
            "Ratio outside permitted range"
        )

    return Fraction(decimal)

def compile_payout(
    rules: PayoutRules
) -> CompiledPayout:

    if not isinstance(rules, PayoutRules):

        raise PayoutConfigurationError(
            "Expected PayoutRules"
        )

    try:

        compiled = CompiledPayout(
            profit_split=compile_ratio(
                rules.profit_split,
                allow_zero=True
            ),

            minimum_amount_cents=dollars_to_cents(
                rules.minimum_amount
            ),

            maximum_amount_cents=optional_cents(
                rules.maximum_amount
            ),

            minimum_profitable_days=(
                rules.minimum_profitable_days
            ),

            profitable_day_threshold_cents=dollars_to_cents(
                rules.profitable_day_threshold
            ),

            minimum_trading_days=(
                rules.minimum_trading_days
            ),

            minimum_balance_after_payout_cents=optional_cents(
                rules.minimum_balance_after_payout
            ),

            minimum_days_between_payouts=(
                rules.minimum_days_between_payouts
            ),

            maximum_withdrawal_ratio=(
                None
                if rules.maximum_withdrawal_ratio is None
                else compile_ratio(
                    rules.maximum_withdrawal_ratio
                )
            ),

            consistency=rules.consistency,

            maximum_total_payouts_cents=optional_cents(
                rules.maximum_total_payouts
            ),

            maximum_payout_count=(
                rules.maximum_payout_count
            )
        )

    except (TypeError, ValueError) as exc:

        raise PayoutConfigurationError(
            str(exc)
        ) from exc

    if compiled.minimum_amount_cents <= 0:

        raise PayoutConfigurationError(
            "Minimum payout must be positive"
        )

    for maximum in (
        compiled.maximum_amount_cents,
        compiled.maximum_total_payouts_cents
    ):

        if (
            maximum is not None
            and maximum < compiled.minimum_amount_cents
        ):

            raise PayoutConfigurationError(
                "Payout maximum is below minimum"
            )

    for value in (
        compiled.minimum_profitable_days,
        compiled.minimum_trading_days,
        compiled.minimum_days_between_payouts
    ):

        if type(value) is not int or value < 0:

            raise PayoutConfigurationError(
                "Invalid minimum day requirement"
            )

    if compiled.profitable_day_threshold_cents < 0:

        raise PayoutConfigurationError(
            "Profitable day threshold cannot be negative"
        )

    count = compiled.maximum_payout_count

    if count is not None and (
        type(count) is not int or count <= 0
    ):

        raise PayoutConfigurationError(
            "Maximum payout count must be positive"
        )

    if compiled.consistency is not None:

        compile_consistency(
            compiled.consistency
        )

    return compiled


@dataclass(frozen=True)
class PayoutCycleStats:

    trading_days: int

    profitable_days: int

    total_profit_cents: int

    best_day_cents: int


def get_payout_cycle_stats(
    account: AccountState,
    rules: CompiledPayout
) -> PayoutCycleStats:

    sessions = account.payout_cycle_sessions

    trading_days = sum(
        record.was_traded
        for record in sessions
    )

    profitable_days = sum(
        record.was_traded
        and record.net_trading_pnl_cents > 0
        and record.net_trading_pnl_cents
        >= rules.profitable_day_threshold_cents

        for record in sessions
    )

    total_profit = sum(
        record.net_trading_pnl_cents
        for record in sessions
    )

    best_day = max(
        (
            record.net_trading_pnl_cents
            for record in sessions
        ),
        default=0
    )

    return PayoutCycleStats(
        trading_days=trading_days,

        profitable_days=profitable_days,

        total_profit_cents=total_profit,

        best_day_cents=max(0, best_day)
    )

def check_payout_consistency(
    account: AccountState,
    rules: ConsistencyRules
) -> bool:

    if rules.window is ConsistencyWindow.CURRENT_STAGE:

        sessions = account.session_history

    elif rules.window is ConsistencyWindow.SINCE_LAST_PAYOUT:

        sessions = account.payout_cycle_sessions

    elif rules.window is ConsistencyWindow.ACCOUNT_LIFETIME:

        sessions = account.lifetime_session_history
    else:

        raise PayoutConfigurationError(
            "Unsupported consistency window"
        )

    total_profit = sum(
        record.net_trading_pnl_cents
        for record in sessions
    )

    best_day = max(
        (
            record.net_trading_pnl_cents
            for record in sessions
        ),
        default=0
    )

    result = evaluate_consistency(
        total_profit_cents=total_profit,

        best_day_cents=max(0, best_day),

        maximum_ratio=compile_consistency(rules)
    )

    return result.satisfied

def calculate_maximum_withdrawal(
    account: AccountState,
    rules: CompiledPayout
) -> int:

    # Only withdraw accumulated account profit.

    available_profit = max(
        0,
        account.balance_profit_cents
    )

    limits = [available_profit]

    # Individual payout maximum.

    if rules.maximum_amount_cents is not None:

        limits.append(
            rules.maximum_amount_cents
        )

    # Fraction of accumulated withdrawable profit.

    if rules.maximum_withdrawal_ratio is not None:

        ratio = rules.maximum_withdrawal_ratio

        ratio_limit = (
            available_profit
            * ratio.numerator
            // ratio.denominator
        )

        limits.append(ratio_limit)

    # Required post-withdrawal account balance.

    if (
        rules.minimum_balance_after_payout_cents
        is not None
    ):

        limits.append(
            account.balance_cents
            - rules.minimum_balance_after_payout_cents
        )

    # Remaining cumulative gross payout allowance.

    if rules.maximum_total_payouts_cents is not None:

        limits.append(
            rules.maximum_total_payouts_cents
            - account.lifetime_gross_withdrawals_cents
        )

    # Do not authorize a withdrawal that would
    # immediately violate the existing drawdown.

    drawdown = account.stage_rules.drawdown

    if drawdown is not None:

        if account.drawdown_floor_cents is None:

            raise PayoutConfigurationError(
                "Drawdown state is not initialized"
            )

        remaining = account.remaining_drawdown_cents

        if drawdown.breach_on_equal:

            remaining -= 1

        limits.append(remaining)

    return max(
        0,
        min(limits)
    )

class PayoutRejectionReason(str, Enum):

    ACCOUNT_INACTIVE = "account_inactive"

    INVALID_STAGE = "invalid_stage"

    ACTIVE_SESSION = "active_session"

    POSITIONS_NOT_FLAT = "positions_not_flat"

    MINIMUM_TRADING_DAYS = "minimum_trading_days"

    MINIMUM_PROFITABLE_DAYS = "minimum_profitable_days"

    CONSISTENCY = "consistency"

    COOLDOWN = "cooldown"

    PAYOUT_COUNT_LIMIT = "payout_count_limit"

    BELOW_MINIMUM = "below_minimum"

    ABOVE_MAXIMUM = "above_maximum"

    INVALID_TIMESTAMP = "invalid_timestamp"


@dataclass(frozen=True)
class PayoutDecision:

    maximum_withdrawal_cents: int

    reasons: tuple[PayoutRejectionReason, ...] = ()

    @property
    def allowed(self) -> bool:

        return not self.reasons


class PayoutRejectedError(ValueError):

    def __init__(self, decision: PayoutDecision):

        self.decision = decision

        message = ", ".join(
            reason.value
            for reason in decision.reasons
        )

        super().__init__(
            f"Payout rejected: {message}"
        )

def assess_payout(
    account: AccountState,
    rules: CompiledPayout,
    event: PayoutRequested
) -> PayoutDecision:

    reasons = []

    if not account.is_active:

        reasons.append(
            PayoutRejectionReason.ACCOUNT_INACTIVE
        )

    if account.stage not in (
        AccountStage.FUNDED,
        AccountStage.LIVE
    ):

        reasons.append(
            PayoutRejectionReason.INVALID_STAGE
        )

    if account.has_active_session:

        reasons.append(
            PayoutRejectionReason.ACTIVE_SESSION
        )

    if (
        event.positions_flat is not True
        or account.unrealized_pnl_cents != 0
    ):

        reasons.append(
            PayoutRejectionReason.POSITIONS_NOT_FLAT
        )

    if (
        account.last_event_timestamp is not None
        and event.timestamp < account.last_event_timestamp
    ):

        reasons.append(
            PayoutRejectionReason.INVALID_TIMESTAMP
        )

    if reasons:

        return PayoutDecision(
            maximum_withdrawal_cents=0,
            reasons=tuple(reasons)
        )

    maximum = calculate_maximum_withdrawal(
        account,
        rules
    )

    stats = get_payout_cycle_stats(
        account,
        rules
    )

    if stats.trading_days < rules.minimum_trading_days:

        reasons.append(
            PayoutRejectionReason.MINIMUM_TRADING_DAYS
        )

    if (
        stats.profitable_days
        < rules.minimum_profitable_days
    ):

        reasons.append(
            PayoutRejectionReason.MINIMUM_PROFITABLE_DAYS
        )

    # Apply payout consistency and any configured
    # stage-level consistency as eligibility gates.

    for consistency in (
        rules.consistency,
        account.stage_rules.consistency
    ):

        if (
            consistency is not None
            and not check_payout_consistency(
                account,
                consistency
            )
        ):

            reasons.append(
                PayoutRejectionReason.CONSISTENCY
            )

            break

    if account.last_payout_timestamp is not None:

        next_allowed = (
            account.last_payout_timestamp
            + timedelta(
                days=rules.minimum_days_between_payouts
            )
        )

        if event.timestamp < next_allowed:

            reasons.append(
                PayoutRejectionReason.COOLDOWN
            )

    if (
        rules.maximum_payout_count is not None
        and account.lifetime_payout_count
        >= rules.maximum_payout_count
    ):

        reasons.append(
            PayoutRejectionReason.PAYOUT_COUNT_LIMIT
        )

    if event.amount_cents < rules.minimum_amount_cents:

        reasons.append(
            PayoutRejectionReason.BELOW_MINIMUM
        )

    if event.amount_cents > maximum:

        reasons.append(
            PayoutRejectionReason.ABOVE_MAXIMUM
        )

    return PayoutDecision(
        maximum_withdrawal_cents=maximum,
        reasons=tuple(reasons)
    )

def calculate_trader_payout(
    gross_amount_cents: int,
    profit_split: Fraction
) -> int:

    return (
        gross_amount_cents
        * profit_split.numerator
        // profit_split.denominator
    )

def process_payout(
    account: AccountState,
    rules: CompiledPayout,
    event: PayoutRequested
) -> PayoutRecord:

    decision = assess_payout(
        account,
        rules,
        event
    )

    if not decision.allowed:

        raise PayoutRejectedError(
            decision
        )

    gross = event.amount_cents

    trader_amount = calculate_trader_payout(
        gross,
        rules.profit_split
    )

    balance_before = account.balance_cents

    balance_after = balance_before - gross

    # Construct and validate the records
    # before mutating the account.

    record = PayoutRecord(
        timestamp=event.timestamp,

        stage=account.stage,

        gross_amount_cents=gross,

        trader_amount_cents=trader_amount,

        balance_before_cents=balance_before,

        balance_after_cents=balance_after
    )

    cash_flow = CashFlow(
        timestamp=event.timestamp,

        type=CashFlowType.PAYOUT,

        amount_cents=trader_amount
    )

    # Apply financial changes.

    account.balance_cents = balance_after

    account.gross_withdrawals_cents += gross

    account.lifetime_gross_withdrawals_cents += gross

    account.payout_count += 1

    account.lifetime_payout_count += 1

    # Preserve the payout records.

    account.payout_history.append(record)

    account.cash_flows.append(cash_flow)

    # Reset the payout-cycle window.

    account.payout_cycle_start_index = len(
        account.session_history
    )

    account.last_payout_timestamp = (
        event.timestamp
    )

    return record