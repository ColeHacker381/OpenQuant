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

