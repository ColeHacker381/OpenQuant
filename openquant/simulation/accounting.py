from .account import (
    AccountState,
    SessionRecord,
    CashFlow
)

from .enums import CashFlowType

from .events import (
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)


class AccountingError(ValueError):
    pass


class AccountingInvariantError(RuntimeError):
    pass

def assert_accounting_invariants(
    account: AccountState
) -> None:

    monetary_fields = (
        "starting_balance_cents",
        "balance_cents",
        "unrealized_pnl_cents",
        "realized_pnl_cents",
        "commissions_cents",
        "gross_withdrawals_cents",
        "session_realized_pnl_cents",
        "lifetime_gross_withdrawals_cents"
    )

    for name in monetary_fields:

        if type(getattr(account, name)) is not int:

            raise AccountingInvariantError(
                f"{name} must be integer cents"
            )

    expected_balance = (
        account.starting_balance_cents
        + account.realized_pnl_cents
        - account.gross_withdrawals_cents
    )

    if account.balance_cents != expected_balance:

        raise AccountingInvariantError(
            f"Balance mismatch: "
            f"expected {expected_balance}, "
            f"found {account.balance_cents}"
        )

    nonnegative_fields = (
        "commissions_cents",
        "trade_count",
        "session_trade_count",
        "payout_count",
        "gross_withdrawals_cents",
        "lifetime_payout_count",
        "lifetime_gross_withdrawals_cents"
    )

    for name in nonnegative_fields:

        value = getattr(account, name)

        if type(value) is not int or value < 0:

            raise AccountingInvariantError(
                f"{name} must be a nonnegative integer"
            )

    if (
        account.session_trade_count
        > account.trade_count
    ):

        raise AccountingInvariantError(
            "Session trade count exceeds stage trade count"
        )

    if (
        account.payout_count
        > account.lifetime_payout_count
    ):

        raise AccountingInvariantError(
            "Stage payout count exceeds lifetime count"
        )

    if (
        account.gross_withdrawals_cents
        > account.lifetime_gross_withdrawals_cents
    ):

        raise AccountingInvariantError(
            "Stage withdrawals exceed lifetime withdrawals"
        )

    if account.has_active_session:

        if (
            type(account.session_start_balance_cents)
            is not int

            or type(account.session_start_equity_cents)
            is not int
        ):

            raise AccountingInvariantError(
                "Active session requires starting values"
            )

    else:

        if (
            account.session_start_balance_cents is not None
            or account.session_start_equity_cents is not None
            or account.session_realized_pnl_cents != 0
            or account.session_trade_count != 0
            or account.session_locked
        ):

            raise AccountingInvariantError(
                "Inactive session contains stale state"
            )


def start_session(
    account: AccountState,
    event: SessionStarted
) -> None:

    if account.has_active_session:

        raise AccountingError(
            "A trading session is already active"
        )

    if any(
        record.session_id == event.session_id

        for record in account.session_history
    ):

        raise AccountingError(
            f"Session already completed: {event.session_id}"
        )

    account.session_id = event.session_id

    account.session_start_balance_cents = (
        account.balance_cents
    )

    account.session_start_equity_cents = (
        account.equity_cents
    )

    account.session_realized_pnl_cents = 0

    account.session_trade_count = 0

    account.session_locked = False

def update_equity(
    account: AccountState,
    event: EquityUpdated
) -> None:

    if not account.has_active_session:

        raise AccountingError(
            "Equity update requires an active session"
        )

    account.unrealized_pnl_cents = (
        event.unrealized_pnl_cents
    )

def realize_trade(
    account: AccountState,
    event: TradeRealized
) -> None:

    if not account.has_active_session:

        raise AccountingError(
            "Realized trade requires an active session"
        )

    net_pnl = event.net_pnl_cents

    # Update stage-level accounting
    account.balance_cents += net_pnl

    account.realized_pnl_cents += net_pnl

    account.commissions_cents += (
        event.commission_cents
    )

    account.trade_count += 1

    # Update session-level accounting
    account.session_realized_pnl_cents += (
        net_pnl
    )

    account.session_trade_count += 1

    # Replace aggregate unrealized PnL
    account.unrealized_pnl_cents = (
        event.remaining_unrealized_pnl_cents
    )

def end_session(
    account: AccountState,
    event: SessionEnded
) -> None:

    if not account.has_active_session:

        raise AccountingError(
            "No active session to end"
        )

    if account.session_id != event.session_id:

        raise AccountingError(
            "Session ID does not match the active session"
        )

    record = SessionRecord(
        session_id=account.session_id,

        starting_balance_cents=(
            account.session_start_balance_cents
        ),

        ending_balance_cents=(
            account.balance_cents
        ),

        net_trading_pnl_cents=(
            account.session_realized_pnl_cents
        ),

        trade_count=(
            account.session_trade_count
        )
    )

    account.session_history.append(record)

    # Clear temporary session state
    account.session_id = None

    account.session_start_balance_cents = None

    account.session_start_equity_cents = None

    account.session_realized_pnl_cents = 0

    account.session_trade_count = 0

    account.session_locked = False


def record_cash_flow(
    account: AccountState,
    flow: CashFlow
) -> None:

    if not isinstance(flow, CashFlow):

        raise TypeError(
            "Expected a CashFlow instance"
        )

    if flow.type is CashFlowType.PAYOUT:

        raise AccountingError(
            "Payout accounting requires the payout engine"
        )

    account.cash_flows.append(flow)