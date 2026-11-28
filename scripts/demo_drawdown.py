from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    BalanceReference,
    FeeRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.account import create_account

from openquant.simulation.engine import AccountEngine

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)

from openquant.simulation.money import format_money


def build_revision(drawdown_type):

    return FirmRevision(
        firm=PropFirm(
            name="Example Trading",

            ruleset=Ruleset(
                account_name="25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,

                    drawdown=DrawdownRules(
                        type=drawdown_type,
                        amount=1000,
                        reference=BalanceReference.EQUITY,
                        breach_reference=BalanceReference.EQUITY
                    ),

                    fees=FeeRules()
                )
            )
        ),

        metadata=RevisionMetadata()
    )


def build_events():

    t = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    return [
        SessionStarted(t, "day_1"),

        EquityUpdated(
            t + timedelta(minutes=1),
            80_000
        ),

        TradeRealized(
            t + timedelta(minutes=2),
            50_000
        ),

        SessionEnded(
            t + timedelta(hours=6),
            "day_1"
        ),

        SessionStarted(
            t + timedelta(days=1),
            "day_2"
        ),

        EquityUpdated(
            t + timedelta(days=1, minutes=1),
            60_000
        ),

        EquityUpdated(
            t + timedelta(days=1, minutes=2),
            -50_000
        ),

        TradeRealized(
            t + timedelta(days=1, minutes=3),
            -30_000
        ),

        SessionEnded(
            t + timedelta(days=1, hours=6),
            "day_2"
        )
    ]


def run(drawdown_type, events):

    account = create_account(
        drawdown_type.value,
        build_revision(drawdown_type),
        SimulationMode.EVALUATION_ONLY,
        require_verified=False
    )

    engine = AccountEngine(account)

    print(f"\n{drawdown_type.value.upper()}")
    print("-" * 70)

    for event in events:

        engine.process(event)

        print(
            f"{type(event).__name__:<16} | "
            f"Equity: "
            f"{format_money(account.equity_cents):>12} | "
            f"Floor: "
            f"{format_money(account.drawdown_floor_cents):>12}"
        )

        if not account.is_active:

            print("ACCOUNT TERMINATED")

            breach = engine.last_drawdown_breach

            print(
                "Drawdown overshoot:",
                format_money(breach.overshoot_cents)
            )

            break

    print(f"Final status: {account.status.value}")
    print(f"Processed events: {engine.processed_event_count}")


def main():

    events = build_events()

    for drawdown_type in DrawdownType:

        run(drawdown_type, events)


if __name__ == "__main__":
    main()