from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    DailyLossRules,
    BreachAction,
    TradingRules,
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
    TradeRealized
)

from openquant.simulation.trading import (
    PositionExposure,
    TradingRequest
)

from openquant.simulation.money import format_money


def build_engine():

    firm = PropFirm(
        "Example Trading",

        Ruleset(
            account_name="25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,

                drawdown=DrawdownRules(
                    DrawdownType.STATIC,
                    1000
                ),

                fees=FeeRules(),

                daily_loss=DailyLossRules(
                    amount=500,
                    action=BreachAction.LOCK_SESSION
                ),

                trading=TradingRules(
                    max_mini_contracts=2,
                    max_micro_contracts=20
                )
            )
        )
    )

    revision = FirmRevision(
        firm=firm,
        metadata=RevisionMetadata()
    )

    account = create_account(
        "account_001",
        revision,
        SimulationMode.EVALUATION_ONLY,
        require_verified=False
    )

    return AccountEngine(account)


def make_request(current, proposed):

    return TradingRequest(
        current=PositionExposure(
            mini_contracts=current
        ),

        proposed=PositionExposure(
            mini_contracts=proposed
        ),

        during_news=False,
        requires_overnight=False,
        requires_weekend=False
    )


def print_decision(engine, request):

    decision = engine.authorize_trade(
        request
    )

    print(
        f"Proposed exposure: "
        f"{request.proposed.mini_contracts}"
    )

    print(f"Authorized: {decision.allowed}")

    for reason in decision.reasons:

        print(f"  Reason: {reason.value}")


def main():

    engine = build_engine()

    account = engine.account

    t = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    engine.process(
        SessionStarted(t, "day_1")
    )

    print("INITIAL AUTHORIZATION")

    print_decision(
        engine,
        make_request(0, 2)
    )

    print_decision(
        engine,
        make_request(0, 3)
    )

    for i in range(2):

        engine.process(
            TradeRealized(
                t + timedelta(minutes=i + 1),
                gross_pnl_cents=-25_000
            )
        )

    print("\nAFTER TWO LOSSES")

    print(
        "Balance:",
        format_money(account.balance_cents)
    )

    print(
        "Session locked:",
        account.session_locked
    )

    print(
        "Account active:",
        account.is_active
    )

    print_decision(
        engine,
        make_request(0, 1)
    )

    engine.process(
        SessionEnded(
            t + timedelta(hours=6),
            "day_1"
        )
    )

    engine.process(
        SessionStarted(
            t + timedelta(days=1),
            "day_2"
        )
    )

    print("\nNEXT SESSION")

    print(
        "Session locked:",
        account.session_locked
    )

    print(
        "Daily loss floor:",
        format_money(
            account.daily_loss_floor_cents
        )
    )

    print_decision(
        engine,
        make_request(0, 1)
    )

    engine.process(
        TradeRealized(
            t + timedelta(days=1, minutes=1),
            gross_pnl_cents=-50_000
        )
    )

    print("\nFINAL ACCOUNT STATUS")

    print(account)

    print(
        "Termination reason:",
        account.termination_reason.value
    )


if __name__ == "__main__":
    main()