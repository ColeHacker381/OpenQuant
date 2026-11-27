from dataclasses import dataclass
from enum import Enum

class DrawdownType(str, Enum):
    STATIC = "static"
    TRAILING_EOD = "trailing_eod"
    TRAILING_INTRADAY = "trailing_intraday"

class BalanceReference(str, Enum):
    BALANCE = "balance"
    EQUITY = "equity"


@dataclass(frozen=True)
class Ruleset:
    account_name: str
    account_size: int
    evaluation: EvaluationRules | None = None
    funded: FundedRules | None = None
    live: LiveRules | None = None
    max_accounts: int | None = None

    def validate(self):

        from .validation import validate_ruleset
        return validate_ruleset(self)

    def validate_for_simulation(self, mode=None):

        from .validation import (
            SimulationMode,
            validate_simulation_readiness
        )

        if mode is None:
            mode = SimulationMode.FULL_LIFECYCLE

        return validate_simulation_readiness(
            self,
            mode
        )


@dataclass(frozen=True)
class DrawdownRules:
    type: DrawdownType
    amount: float

    trailing_stop_balance: float | None = None
    reference: BalanceReference = BalanceReference.BALANCE
    breach_reference: BalanceReference = BalanceReference.EQUITY
    breach_on_equal: bool = True

@dataclass
class FeeRules:
    purchase: float = 0.0
    activation: float = 0.0
    recurring: float = 0.0
    recurring_interval_days: int | None = None
    reset: float | None = None

class BreachAction(str, Enum):
    TERMINATE = "terminate"
    LOCK_SESSION = "lock_session"

@dataclass(frozen=True)
class DailyLossRules:
    amount: float
    action: BreachAction = BreachAction.TERMINATE
    reference: BalanceReference = BalanceReference.EQUITY
    breach_on_equal: bool = True

class ConsistencyWindow(str, Enum):
    CURRENT_STAGE = "current_stage"
    SINCE_LAST_PAYOUT = "since_last_payout"
    ACCOUNT_LIFETIME = "account_lifetime"

@dataclass(frozen=True)
class ConsistencyRules:
    maximum_ratio: float
    window: ConsistencyWindow = ConsistencyWindow.CURRENT_STAGE

@dataclass(frozen=True)
class TradingRules:
    max_mini_contracts: int | None = None
    max_micro_contracts: int | None = None

    allow_overnight: bool = False
    allow_weekend: bool = False
    allow_news: bool = True

@dataclass(frozen=True)
class PayoutRules:
    profit_split: float
    minimum_amount: float
    maximum_amount: float | None = None
    minimum_profitable_days: int = 0
    profitable_day_threshold: float = 0.0
    minimum_trading_days: int = 0
    minimum_balance_after_payout: float | None = None
    minimum_days_between_payouts: int = 0
    maximum_withdrawal_ratio: float | None = None
    consistency: ConsistencyRules | None = None
    maximum_total_payouts: float | None = None
    maximum_payout_count: int | None = None

class TransitionMode(str, Enum):
    ALL = "all"
    ANY = "any"

class TransitionType(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL_REVIEW = "manual_review"

@dataclass(frozen=True)
class LiveTransitionRules:
    minimum_payout_count: int | None = None
    minimum_total_payouts: float | None = None
    minimum_trading_days: int | None = None
    minimum_profit: float | None = None
    mode: TransitionMode = TransitionMode.ALL
    transition_type: TransitionType = TransitionType.AUTOMATIC

@dataclass(frozen=True)
class EvaluationRules:
    profit_target: float
    drawdown: DrawdownRules
    fees: FeeRules
    minimum_trading_days: int = 0
    maximum_trading_days: int | None = None
    maximum_calendar_days: int | None = None
    daily_loss: DailyLossRules | None = None
    consistency: ConsistencyRules | None = None
    trading: TradingRules | None = None

@dataclass(frozen=True)
class FundedRules:
    drawdown: DrawdownRules
    fees: FeeRules
    payout: PayoutRules
    daily_loss: DailyLossRules | None = None
    consistency: ConsistencyRules | None = None
    trading: TradingRules | None = None
    live_transition: LiveTransitionRules | None = None

@dataclass(frozen=True)
class LiveRules:
    drawdown: DrawdownRules | None
    fees: FeeRules
    payout: PayoutRules
    starting_balance: float | None = None
    daily_loss: DailyLossRules | None = None
    consistency: ConsistencyRules | None = None
    trading: TradingRules | None = None



    