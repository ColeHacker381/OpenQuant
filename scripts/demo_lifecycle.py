from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    FundedRules,
    LiveRules,
    LiveTransitionRules,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    PayoutRules,
    ConsistencyRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.simulation.lifecycle import (
    AccountLifecycle
)

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    PayoutRequested
)

from openquant.simulation.money import format_money


def build_revision():

    drawdown = DrawdownRules(
        DrawdownType.STATIC,
        1000
    )

    firm = PropFirm(
        "OpenQuant Research",

        Ruleset(
            account_name="Hypothetical 25K",

            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1250,

                drawdown=drawdown,

                fees=FeeRules(
                    purchase=100,
                    reset=75
                ),

                minimum_trading_days=3,

                consistency=ConsistencyRules(0.40)
            ),

            funded=FundedRules(
                drawdown=drawdown,

                fees=FeeRules(
                    activation=50
                ),

                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250
                ),

                live_transition=LiveTransitionRules(
                    minimum_payout_count=2
                )
            ),

            live=LiveRules(
                starting_balance=10_000,

                drawdown=DrawdownRules(
                    DrawdownType.STATIC,
                    2000
                ),

                fees=FeeRules(
                    activation=25
                ),

                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250
                )
            )
        )
    )

    return FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            notes="Fictional lifecycle demonstration."
        )
    )


def trade_day(
    lifecycle,
    timestamp,
    pnl_cents
):

    session_id = timestamp.date().isoformat()

    lifecycle.process(
        SessionStarted(
            timestamp,
            session_id
        )
    )

    if lifecycle.account.is_active:

        lifecycle.process(
            TradeRealized(
                timestamp + timedelta(minutes=1),
                pnl_cents
            )
        )

    if lifecycle.account.is_active:

        lifecycle.process(
            SessionEnded(
                timestamp + timedelta(hours=1),

                session_id,

                positions_flat=True
            )
        )


def print_state(label, lifecycle):

    print(f"\n{label}")
    print(lifecycle.account)

    print(
        "External net cash:",
        format_money(
            lifecycle.account.net_cash_flow_cents
        )
    )


def main():

    start = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    lifecycle = AccountLifecycle.purchase(
        "account_001",

        build_revision(),

        start,

        require_verified=False
    )

    print_state(
        "EVALUATION PURCHASED",
        lifecycle
    )

    # Fail the first evaluation.

    trade_day(
        lifecycle,
        start,
        -100_000
    )

    print_state(
        "FIRST EVALUATION FAILED",
        lifecycle
    )

    # Reset and start a fresh evaluation.

    lifecycle.reset(
        start + timedelta(days=1)
    )

    print_state(
        "EVALUATION RESET",
        lifecycle
    )

    for day, pnl in enumerate(
        [50_000, 50_000, 25_000],
        start=1
    ):

        trade_day(
            lifecycle,

            start + timedelta(days=day),

            pnl
        )

    print_state(
        "EVALUATION PASSED",
        lifecycle
    )

    # Activate funded stage.

    lifecycle.activate(
        start + timedelta(days=4)
    )

    print_state(
        "FUNDED ACCOUNT ACTIVATED",
        lifecycle
    )

    # Receive two funded payouts.

    for day in (4, 5):

        timestamp = start + timedelta(days=day)

        trade_day(
            lifecycle,

            timestamp,

            100_000
        )

        lifecycle.process(
            PayoutRequested(
                timestamp + timedelta(hours=2),

                50_000,

                positions_flat=True
            )
        )

        print_state(
            "FUNDED PAYOUT RECEIVED",
            lifecycle
        )

    # Two payouts trigger live qualification.

    lifecycle.activate(
        start + timedelta(days=6)
    )

    print_state(
        "LIVE ACCOUNT ACTIVATED",
        lifecycle
    )

    timestamp = start + timedelta(days=6)

    trade_day(
        lifecycle,
        timestamp,
        100_000
    )

    lifecycle.process(
        PayoutRequested(
            timestamp + timedelta(hours=2),

            50_000,

            positions_flat=True
        )
    )

    print_state(
        "LIVE PAYOUT RECEIVED",
        lifecycle
    )

    # Final drawdown breach.

    trade_day(
        lifecycle,

        start + timedelta(days=7),

        -250_000
    )

    print_state(
        "ACCOUNT TERMINATED",
        lifecycle
    )

    result = lifecycle.result()

    print("\nEXTERNAL CASH LEDGER")
    print("-" * 50)

    for flow in lifecycle.account.cash_flows:

        print(
            f"{flow.type.value:<18}"
            f"{format_money(flow.amount_cents):>15}"
        )

    print("\nLIFECYCLE SUMMARY")
    print("-" * 50)

    print(
        "Evaluation attempts:",
        result.evaluation_attempts
    )

    print("Resets:", result.reset_count)

    print(
        "Evaluation passed:",
        result.evaluation_passed
    )

    print(
        "Reached funded:",
        result.reached_funded
    )

    print(
        "Reached live:",
        result.reached_live
    )

    print("Payouts:", result.payout_count)

    print(
        "Expenses:",
        format_money(result.expenses_cents)
    )

    print(
        "Payout revenue:",
        format_money(result.payout_revenue_cents)
    )

    print(
        "Net cash:",
        format_money(result.net_cash_cents)
    )


if __name__ == "__main__":

    main()