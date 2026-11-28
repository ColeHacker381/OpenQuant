from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import (
    create_account,
    CashFlow
)

from openquant.simulation.enums import CashFlowType

from openquant.simulation.engine import AccountEngine

from openquant.simulation.accounting import record_cash_flow

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)

from openquant.simulation.money import format_money


def main():

    firm = PropFirm(
        name="Example Trading",

        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,

                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000
                ),

                fees=FeeRules(purchase=100)
            )
        )
    )

    revision = FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            notes="Fictional accounting demonstration."
        )
    )

    account = create_account(
        "account_001",
        revision,
        SimulationMode.EVALUATION_ONLY,
        require_verified=False
    )

    engine = AccountEngine(
        account,
        strict=True
    )

    start = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    record_cash_flow(
        account,

        CashFlow(
            timestamp=start,
            type=CashFlowType.PURCHASE,
            amount_cents=-10_000
        )
    )

    events = [
        SessionStarted(
            start,
            "2026-01-05"
        ),

        EquityUpdated(
            start + timedelta(minutes=1),
            40_000
        ),

        TradeRealized(
            start + timedelta(minutes=2),
            gross_pnl_cents=40_000,
            commission_cents=500
        ),

        EquityUpdated(
            start + timedelta(minutes=3),
            -25_000
        ),

        TradeRealized(
            start + timedelta(minutes=4),
            gross_pnl_cents=-25_000,
            commission_cents=500
        ),

        EquityUpdated(
            start + timedelta(minutes=5),
            50_000
        ),

        TradeRealized(
            start + timedelta(minutes=6),
            gross_pnl_cents=50_000,
            commission_cents=500
        ),

        SessionEnded(
            start + timedelta(hours=6),
            "2026-01-05"
        )
    ]

    print("ACCOUNT EVENT PROCESSING")
    print("-" * 65)

    for event in events:

        engine.process(event)

        print(
            f"{type(event).__name__:<20} | "
            f"Balance: "
            f"{format_money(account.balance_cents):>12} | "
            f"Equity: "
            f"{format_money(account.equity_cents):>12}"
        )

    print("\nFINAL ACCOUNT SUMMARY")
    print("-" * 65)

    metrics = {
        "Starting balance": account.starting_balance_cents,
        "Ending balance": account.balance_cents,
        "Gross realized PnL": account.gross_realized_pnl_cents,
        "Net realized PnL": account.realized_pnl_cents,
        "Commissions": account.commissions_cents,
        "External cash flow": account.net_cash_flow_cents
    }

    for label, value in metrics.items():

        print(
            f"{label:<25}: {format_money(value)}"
        )

    print(f"Trades: {account.trade_count}")
    print(f"Trading days: {account.trading_days}")
    print(f"Profitable days: {account.profitable_days}")

    print(
        f"Processed events: "
        f"{engine.processed_event_count}"
    )


if __name__ == "__main__":
    main()