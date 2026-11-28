from datetime import datetime, timezone, timedelta
from statistics import median
from time import perf_counter

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    DailyLossRules,
    BreachAction,
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


TRADE_COUNT = 100_000
REPETITIONS = 5


def build_revision(
    drawdown_type=DrawdownType.STATIC,
    *,
    enable_daily_loss=False
):

    return FirmRevision(
        firm=PropFirm(
            "Benchmark Firm",

            Ruleset(
                account_name="25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,
                    daily_loss=(
                        DailyLossRules(
                            amount=500,

                            action=(
                                BreachAction.LOCK_SESSION
                            )
                        )

                        if enable_daily_loss
                        else None
                    ),

                    drawdown=DrawdownRules(
                        drawdown_type,
                        1000
                    ),

                    fees=FeeRules()
                )
            )
        ),

        metadata=RevisionMetadata()
    )


def build_events():

    timestamp = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    events = [
        SessionStarted(
            timestamp,
            "benchmark_session"
        )
    ]

    for index in range(TRADE_COUNT):

        pnl = (
            1100
            if index % 2 == 0
            else -900
        )

        events.append(
            EquityUpdated(
                timestamp + timedelta(
                    milliseconds=index * 2
                ),
                pnl
            )
        )

        events.append(
            TradeRealized(
                timestamp + timedelta(
                    milliseconds=index * 2 + 1
                ),
                gross_pnl_cents=pnl,
                commission_cents=100
            )
        )

    events.append(
        SessionEnded(
            timestamp + timedelta(hours=1),
            "benchmark_session"
        )
    )

    return tuple(events)


def benchmark(revision, events, strict):

    runtimes = []

    for _ in range(REPETITIONS):

        account = create_account(
            "benchmark_account",
            revision,
            SimulationMode.EVALUATION_ONLY,
            require_verified=False
        )

        engine = AccountEngine(
            account,
            strict=strict
        )

        start = perf_counter()

        engine.process_all(events)

        elapsed = perf_counter() - start

        runtimes.append(elapsed)

        assert account.realized_pnl_cents == 0

        assert account.is_active

    runtime = median(runtimes)

    throughput = len(events) / runtime

    print(f"Strict mode: {strict}")
    print(f"Trades: {TRADE_COUNT:,}")
    print(f"Events: {len(events):,}")
    print(f"Median runtime: {runtime:.6f} s")
    print(f"Throughput: {throughput:,.0f} events/s")
    print()

def main():

    events = build_events()

    for drawdown_type in DrawdownType:

        for enable_daily_loss in (False, True):

            revision = build_revision(
                drawdown_type,

                enable_daily_loss=enable_daily_loss
            )

            print("=" * 50)

            print(
                f"Drawdown type: "
                f"{drawdown_type.value}"
            )

            print(
                f"Daily loss enabled: "
                f"{enable_daily_loss}"
            )

            benchmark(
                revision,
                events,
                strict=False
            )

            benchmark(
                revision,
                events,
                strict=True
            )
            
if __name__ == "__main__":
    main()