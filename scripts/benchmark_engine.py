from datetime import datetime, timezone, timedelta
from statistics import median
from time import perf_counter

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


def build_revision():

    return FirmRevision(
        firm=PropFirm(
            "Benchmark Firm",

            Ruleset(
                account_name="25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,

                    drawdown=DrawdownRules(
                        DrawdownType.STATIC,
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
            1000
            if index % 2 == 0
            else -1000
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

        assert account.trade_count == TRADE_COUNT

        assert engine.processed_event_count == len(events)

        assert (
            account.realized_pnl_cents
            == -TRADE_COUNT * 100
        )

    runtime = median(runtimes)

    throughput = len(events) / runtime

    print(f"Strict mode: {strict}")
    print(f"Trades: {TRADE_COUNT:,}")
    print(f"Events: {len(events):,}")
    print(f"Median runtime: {runtime:.6f} s")
    print(f"Throughput: {throughput:,.0f} events/s")
    print()


def main():

    revision = build_revision()

    events = build_events()

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