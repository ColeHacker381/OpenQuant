from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    FundedRules,
    DrawdownRules,
    DrawdownType,
    PayoutRules,
    ConsistencyRules,
    ConsistencyWindow,
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

from openquant.simulation.accounting import (
    record_cash_flow,
    audit_payout_accounting
)

from openquant.simulation.enums import CashFlowType

from openquant.simulation.engine import AccountEngine

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    PayoutRequested
)

from openquant.simulation.money import format_money


def build_engine():

    firm = PropFirm(
        "OpenQuant Research",

        Ruleset(
            account_name="Hypothetical 25K",
            account_size=25_000,

            funded=FundedRules(
                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000,
                    trailing_stop_balance=25_100
                ),

                fees=FeeRules(
                    activation=50
                ),

                payout=PayoutRules(
                    profit_split=0.90,

                    minimum_amount=250,

                    maximum_amount=1500,

                    minimum_profitable_days=3,

                    profitable_day_threshold=100,

                    minimum_trading_days=5,

                    minimum_balance_after_payout=25_500,

                    minimum_days_between_payouts=7,

                    maximum_withdrawal_ratio=0.50,

                    consistency=ConsistencyRules(
                        maximum_ratio=0.40,

                        window=(
                            ConsistencyWindow.SINCE_LAST_PAYOUT
                        )
                    )
                )
            )
        )
    )

    revision = FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            notes="Fictional funded payout demonstration."
        )
    )

    account = create_account(
        "funded_001",
        revision,
        SimulationMode.FUNDED_ONLY,
        require_verified=False
    )

    return AccountEngine(account)


def main():

    engine = build_engine()

    account = engine.account

    start = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    record_cash_flow(
        account,

        CashFlow(
            start,
            CashFlowType.ACTIVATION,
            -5000
        )
    )

    daily_profits = [
        50_000,
        30_000,
        40_000,
        30_000,
        50_000
    ]

    for index, pnl in enumerate(daily_profits):

        timestamp = start + timedelta(days=index)

        session_id = f"day_{index + 1}"

        engine.process_all([
            SessionStarted(
                timestamp,
                session_id
            ),

            TradeRealized(
                timestamp + timedelta(minutes=1),
                gross_pnl_cents=pnl
            ),

            SessionEnded(
                timestamp + timedelta(hours=1),

                session_id,

                positions_flat=True
            )
        ])

    print("BEFORE PAYOUT")

    print(account)

    print(
        "Trading profit:",
        format_money(account.realized_pnl_cents)
    )

    print(
        "Drawdown floor:",
        format_money(account.drawdown_floor_cents)
    )

    request = PayoutRequested(
        timestamp=start + timedelta(days=5),

        amount_cents=100_000,

        positions_flat=True
    )

    decision = engine.assess_payout(request)

    print(
        "Maximum withdrawal:",
        format_money(
            decision.maximum_withdrawal_cents
        )
    )

    print("Payout allowed:", decision.allowed)

    if not decision.allowed:

        print(decision.reasons)

        return

    engine.process(request)

    audit_payout_accounting(account)

    print("\nAFTER PAYOUT")

    print(account)

    print(
        "Gross withdrawal:",
        format_money(
            engine.last_payout.gross_amount_cents
        )
    )

    print(
        "Trader payout:",
        format_money(
            engine.last_payout.trader_amount_cents
        )
    )

    print(
        "External net cash:",
        format_money(account.net_cash_flow_cents)
    )

    print(
        "Remaining drawdown:",
        format_money(
            account.remaining_drawdown_cents
        )
    )

    print(
        "Payout count:",
        account.payout_count
    )

    print(
        "Current cycle trading days:",
        len(account.payout_cycle_sessions)
    )

    print(
        "Historical sessions:",
        len(account.session_history)
    )


if __name__ == "__main__":
    main()