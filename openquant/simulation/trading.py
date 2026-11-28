from dataclasses import dataclass
from enum import Enum

from ..propfirm.ruleset import TradingRules

from .account import AccountState

@dataclass(frozen=True)
class PositionExposure:

    mini_contracts: int = 0

    micro_contracts: int = 0

    def __post_init__(self):

        for name in (
            "mini_contracts",
            "micro_contracts"
        ):

            if type(getattr(self, name)) is not int:

                raise TypeError(
                    f"{name} must be an integer"
                )

    @property
    def is_flat(self) -> bool:

        return (
            self.mini_contracts == 0
            and self.micro_contracts == 0
        )


@dataclass(frozen=True)
class TradingRequest:

    current: PositionExposure

    proposed: PositionExposure

    during_news: bool

    requires_overnight: bool

    requires_weekend: bool

    def __post_init__(self):

        if not isinstance(
            self.current,
            PositionExposure
        ):

            raise TypeError(
                "Invalid current exposure"
            )

        if not isinstance(
            self.proposed,
            PositionExposure
        ):

            raise TypeError(
                "Invalid proposed exposure"
            )

        for name in (
            "during_news",
            "requires_overnight",
            "requires_weekend"
        ):

            if type(getattr(self, name)) is not bool:

                raise TypeError(
                    f"{name} must be boolean"
                )

def is_quantity_reduction(
    current: int,
    proposed: int
) -> bool:

    # Closing a position is always a reduction.
    if proposed == 0:

        return True

    # Opening a position from flat adds risk.
    if current == 0:

        return False

    # Crossing through zero opens new exposure.
    if (current > 0) != (proposed > 0):

        return False

    return abs(proposed) <= abs(current)

def is_reduce_only(
    request: TradingRequest
) -> bool:

    return (
        is_quantity_reduction(
            request.current.mini_contracts,
            request.proposed.mini_contracts
        )

        and is_quantity_reduction(
            request.current.micro_contracts,
            request.proposed.micro_contracts
        )
    )

class TradingRejectionReason(str, Enum):

    ACCOUNT_INACTIVE = "account_inactive"

    NO_ACTIVE_SESSION = "no_active_session"

    SESSION_LOCKED = "session_locked"

    MINI_LIMIT = "mini_limit"

    MICRO_LIMIT = "micro_limit"

    MIXED_EXPOSURE_UNSUPPORTED = (
        "mixed_exposure_unsupported"
    )

    NEWS_RESTRICTED = "news_restricted"

    OVERNIGHT_RESTRICTED = "overnight_restricted"

    WEEKEND_RESTRICTED = "weekend_restricted"


@dataclass(frozen=True)
class TradingDecision:

    reasons: tuple[TradingRejectionReason, ...] = ()

    @property
    def allowed(self) -> bool:

        return not self.reasons

def authorize_trade(
    account: AccountState,
    request: TradingRequest
) -> TradingDecision:

    if not isinstance(request, TradingRequest):

        raise TypeError(
            "Expected TradingRequest"
        )

    reasons = []

    if not account.is_active:

        reasons.append(
            TradingRejectionReason.ACCOUNT_INACTIVE
        )

    if not account.has_active_session:

        reasons.append(
            TradingRejectionReason.NO_ACTIVE_SESSION
        )

    if reasons:

        return TradingDecision(
            tuple(reasons)
        )

    # Risk-reducing requests remain permitted,
    # including during daily session locks.
    if is_reduce_only(request):

        return TradingDecision()

    if account.session_locked:

        reasons.append(
            TradingRejectionReason.SESSION_LOCKED
        )

    rules = account.stage_rules.trading

    proposed = request.proposed

    mini = abs(proposed.mini_contracts)

    micro = abs(proposed.micro_contracts)

    # Our existing schema does not establish
    # a combined mini/micro exposure allowance.
    if mini > 0 and micro > 0:

        reasons.append(
            TradingRejectionReason
            .MIXED_EXPOSURE_UNSUPPORTED
        )

    if rules is not None:

        reasons.extend(
            check_trading_rules(
                rules,
                request
            )
        )

    return TradingDecision(
        tuple(reasons)
    )

def check_trading_rules(
    rules: TradingRules,
    request: TradingRequest
) -> tuple[TradingRejectionReason, ...]:

    reasons = []

    proposed = request.proposed

    if (
        rules.max_mini_contracts is not None

        and abs(proposed.mini_contracts)
        > rules.max_mini_contracts
    ):

        reasons.append(
            TradingRejectionReason.MINI_LIMIT
        )

    if (
        rules.max_micro_contracts is not None

        and abs(proposed.micro_contracts)
        > rules.max_micro_contracts
    ):

        reasons.append(
            TradingRejectionReason.MICRO_LIMIT
        )

    if request.during_news and not rules.allow_news:

        reasons.append(
            TradingRejectionReason.NEWS_RESTRICTED
        )

    if (
        request.requires_overnight
        and not rules.allow_overnight
    ):

        reasons.append(
            TradingRejectionReason.OVERNIGHT_RESTRICTED
        )

    if (
        request.requires_weekend
        and not rules.allow_weekend
    ):

        reasons.append(
            TradingRejectionReason.WEEKEND_RESTRICTED
        )

    return tuple(reasons)

