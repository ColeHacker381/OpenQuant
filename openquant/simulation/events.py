from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


@dataclass(frozen=True)
class AccountEvent:

    timestamp: datetime

    def __post_init__(self):

        if not isinstance(self.timestamp, datetime):

            raise TypeError(
                "Timestamp must be a datetime"
            )

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):

            raise ValueError(
                "Timestamp must be timezone-aware"
            )

        object.__setattr__(
            self,
            "timestamp",
            self.timestamp.astimezone(timezone.utc)
        )

def require_cents(
    value,
    name: str,
    *,
    minimum: int | None = None
) -> None:

    if type(value) is not int:

        raise TypeError(
            f"{name} must be integer cents"
        )

    if minimum is not None and value < minimum:

        raise ValueError(
            f"{name} must be at least {minimum}"
        )

@dataclass(frozen=True)
class SessionStarted(AccountEvent):

    session_id: str

    def __post_init__(self):

        super().__post_init__()

        if (
            type(self.session_id) is not str
            or not self.session_id.strip()
        ):

            raise ValueError(
                "Session ID cannot be empty"
            )


@dataclass(frozen=True)
class SessionEnded(AccountEvent):

    session_id: str
    positions_flat: bool | None = None

    def __post_init__(self):

        super().__post_init__()

        if (
            type(self.session_id) is not str
            or not self.session_id.strip()
        ):

            raise ValueError(
                "Session ID cannot be empty"
            )

        if (
            self.positions_flat is not None
            and type(self.positions_flat) is not bool
        ):

            raise TypeError(
                "positions_flat must be boolean or None"
            )

@dataclass(frozen=True)
class EquityUpdated(AccountEvent):

    unrealized_pnl_cents: int

    def __post_init__(self):

        super().__post_init__()

        require_cents(
            self.unrealized_pnl_cents,
            "Unrealized PnL"
        )

@dataclass(frozen=True)
class TradeRealized(AccountEvent):

    gross_pnl_cents: int

    commission_cents: int = 0

    remaining_unrealized_pnl_cents: int = 0

    def __post_init__(self):

        super().__post_init__()

        require_cents(
            self.gross_pnl_cents,
            "Gross PnL"
        )

        require_cents(
            self.commission_cents,
            "Commission",
            minimum=0
        )

        require_cents(
            self.remaining_unrealized_pnl_cents,
            "Remaining unrealized PnL"
        )

    @property
    def net_pnl_cents(self) -> int:

        return (
            self.gross_pnl_cents
            - self.commission_cents
        )

@dataclass(frozen=True)
class PayoutRequested(AccountEvent):

    amount_cents: int

    def __post_init__(self):

        super().__post_init__()

        require_cents(
            self.amount_cents,
            "Payout amount",
            minimum=1
        )


@dataclass(frozen=True)
class StageActivationRequested(AccountEvent):
    pass

