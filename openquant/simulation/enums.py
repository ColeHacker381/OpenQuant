from enum import Enum

class AccountStage(str, Enum):
    EVALUATION = "evaluation"
    FUNDED = "funded"
    LIVE = "live"

class AccountStatus(str, Enum):
    ACTIVE = "active"
    AWAITING_ACTIVATION = "awaiting_activation"
    TERMINATED = "terminated"
    CLOSED = "closed"

class TerminationReason(str, Enum):
    DRAWDOWN = "drawdown"
    DAILY_LOSS = "daily_loss"
    EXPIRATION = "expiration"
    INACTIVITY = "inactivity"
    TRADING_VIOLATION = "trading_violation"
    MANUAL = "manual"

class CashFlowType(str, Enum):
    PURCHASE = "purchase"
    ACTIVATION = "activation"
    RECURRING_FEE = "recurring_fee"
    RESET = "reset"
    PAYOUT = "payout"
    REFUND = "refund"

