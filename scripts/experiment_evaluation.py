from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from collections import Counter
from fractions import Fraction
from random import Random
from statistics import mean, median
from math import sqrt
from time import perf_counter

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    DrawdownRules,
    DrawdownType,
    DailyLossRules,
    BreachAction,
    ConsistencyRules,
    TradingRules,
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

from openquant.simulation.accounting import record_cash_flow

from openquant.simulation.enums import (
    CashFlowType,
    EvaluationOutcome
)

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

from openquant.simulation.money import dollars_to_cents


@dataclass(frozen=True)
class ExperimentConfig:

    simulations: int = 10_000

    maximum_sessions: int = 30

    trades_per_session: int = 5

    win_rate: float = 0.41

    reward_risk: Fraction = Fraction(38, 25)

    commission_cents: int = 500

    seed: int = 42

    strict: bool = False


@dataclass(frozen=True)
class EvaluationTrial:

    outcome: EvaluationOutcome

    sessions_attempted: int

    trades: int

    net_profit_cents: int

    external_cash_cents: int

    termination_reason: str | None

def build_revision() -> FirmRevision:

    firm = PropFirm(
        name="OpenQuant Research",

        ruleset=Ruleset(
            account_name="Hypothetical 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1250,

                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000
                ),

                fees=FeeRules(
                    purchase=100
                ),

                minimum_trading_days=3,

                consistency=ConsistencyRules(
                    maximum_ratio=0.40
                ),

                daily_loss=DailyLossRules(
                    amount=500,

                    action=(
                        BreachAction.LOCK_SESSION
                    )
                ),

                trading=TradingRules(
                    max_micro_contracts=20
                )
            )
        )
    )

    return FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            notes=(
                "Fictional evaluation research. "
                "Not a verified prop firm offering."
            )
        )
    )

def run_trial(
    revision: FirmRevision,
    config: ExperimentConfig,
    risk_cents: int,
    trial_index: int
) -> EvaluationTrial:

    # Each trial has its own deterministic
    # random-number stream.

    rng = Random(
        config.seed + trial_index
    )

    account = create_account(
        f"trial_{trial_index:06d}",
        revision,

        SimulationMode.EVALUATION_ONLY,

        require_verified=False
    )

    engine = AccountEngine(
        account,
        strict=config.strict
    )

    start = datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )

    purchase_fee = dollars_to_cents(
        account.stage_rules.fees.purchase
    )

    record_cash_flow(
        account,

        CashFlow(
            start,
            CashFlowType.PURCHASE,
            -purchase_fee
        )
    )

    # Convert the winning outcome to exact cents.
    # Fractional cents are rounded down.

    reward_cents = (
        risk_cents
        * config.reward_risk.numerator
        // config.reward_risk.denominator
    )

    request = TradingRequest(
        current=PositionExposure(),

        proposed=PositionExposure(
            micro_contracts=1
        ),

        during_news=False,
        requires_overnight=False,
        requires_weekend=False
    )

    sessions_attempted = 0

    for day_index in range(
        config.maximum_sessions
    ):

        if not account.is_active:
            break

        session_id = f"session_{day_index:04d}"

        timestamp = start + timedelta(
            days=day_index
        )

        engine.process(
            SessionStarted(
                timestamp,
                session_id
            )
        )

        sessions_attempted += 1

        if not account.is_active:
            break

        for trade_index in range(
            config.trades_per_session
        ):

            # Consume one outcome for every potential
            # opportunity, even if trading is locked.

            won = (
                rng.random() < config.win_rate
            )

            if not account.is_active:
                break

            decision = engine.authorize_trade(
                request
            )

            if not decision.allowed:
                continue

            pnl = (
                reward_cents
                if won
                else -risk_cents
            )

            engine.process(
                TradeRealized(
                    timestamp=(
                        timestamp
                        + timedelta(
                            minutes=trade_index + 1
                        )
                    ),

                    gross_pnl_cents=pnl,

                    commission_cents=(
                        config.commission_cents
                    ),

                    remaining_unrealized_pnl_cents=0
                )
            )

        if account.is_active:

            engine.process(
                SessionEnded(
                    timestamp + timedelta(hours=6),

                    session_id,

                    positions_flat=True
                )
            )

    reason = account.termination_reason

    return EvaluationTrial(
        outcome=account.evaluation_outcome,

        sessions_attempted=sessions_attempted,

        trades=account.trade_count,

        net_profit_cents=(
            account.realized_pnl_cents
        ),

        external_cash_cents=(
            account.net_cash_flow_cents
        ),

        termination_reason=(
            None if reason is None
            else reason.value
        )
    )


def wilson_interval(
    successes: int,
    trials: int,
    z: float = 1.95996398454
) -> tuple[float, float]:

    if (
        trials <= 0
        or not 0 <= successes <= trials
    ):

        raise ValueError(
            "Invalid binomial sample"
        )

    p = successes / trials

    denominator = 1 + z**2 / trials

    center = (
        p + z**2 / (2 * trials)
    ) / denominator

    margin = (
        z
        * sqrt(
            p * (1 - p) / trials
            + z**2 / (4 * trials**2)
        )
        / denominator
    )

    return (
        max(0.0, center - margin),

        min(1.0, center + margin)
    )

def run_experiment(
    revision,
    config,
    risk_dollars
):

    risk_cents = dollars_to_cents(
        risk_dollars
    )

    start = perf_counter()

    trials = [
        run_trial(
            revision,
            config,
            risk_cents,
            trial_index
        )

        for trial_index in range(
            config.simulations
        )
    ]

    elapsed = perf_counter() - start

    counts = Counter(
        trial.outcome
        for trial in trials
    )

    passed = counts[
        EvaluationOutcome.PASSED
    ]

    failed = counts[
        EvaluationOutcome.FAILED
    ]

    unfinished = counts[
        EvaluationOutcome.IN_PROGRESS
    ]

    pass_probability = (
        passed / config.simulations
    )

    low, high = wilson_interval(
        passed,
        config.simulations
    )

    passing_trials = [
        trial

        for trial in trials

        if trial.outcome
        is EvaluationOutcome.PASSED
    ]

    purchase_fee = dollars_to_cents(
        revision.firm.ruleset.evaluation.fees.purchase
    ) / 100

    # Expected evaluation fees per successful pass
    # when independently purchasing fresh attempts.

    expected_pass_cost = (
        purchase_fee / pass_probability
        if pass_probability > 0
        else float("inf")
    )

    failures = Counter(
        trial.termination_reason

        for trial in trials

        if trial.termination_reason is not None
    )

    result = {
        "risk": risk_dollars,

        "pass_probability": pass_probability,

        "failure_probability": (
            failed / config.simulations
        ),

        "unfinished_probability": (
            unfinished / config.simulations
        ),

        "confidence_low": low,

        "confidence_high": high,

        "mean_trades": mean(
            trial.trades
            for trial in trials
        ),

        "mean_profit": mean(
            trial.net_profit_cents
            for trial in trials
        ) / 100,

        "median_days_to_pass": (
            median(
                trial.sessions_attempted
                for trial in passing_trials
            )

            if passing_trials
            else None
        ),

        "expected_pass_cost": expected_pass_cost,

        "failures": dict(failures),

        "runtime": elapsed
    }

    return result

def main():

    revision = build_revision()

    config = ExperimentConfig()

    risks = [
        50,
        100,
        150,
        200,
        250,
        300
    ]

    results = []

    print("OPENQUANT EVALUATION EXPERIMENT")
    print("=" * 72)

    print(
        f"Simulations per configuration: "
        f"{config.simulations:,}"
    )

    print(f"Win rate: {config.win_rate:.1%}")

    print(
        f"Reward/risk: "
        f"{float(config.reward_risk):.2f}"
    )

    print(
        f"Horizon: "
        f"{config.maximum_sessions} sessions"
    )

    print(f"Random seed: {config.seed}")

    for risk in risks:

        result = run_experiment(
            revision,
            config,
            risk
        )

        results.append(result)

        days = result["median_days_to_pass"]

        days_label = (
            "N/A"
            if days is None
            else f"{days:.1f}"
        )

        print("\n" + "-" * 72)

        print(f"Risk per trade: ${risk}")

        print(
            f"Pass probability: "
            f"{result['pass_probability']:.2%}"
        )

        print(
            f"95% confidence interval: "
            f"{result['confidence_low']:.2%} - "
            f"{result['confidence_high']:.2%}"
        )

        print(
            f"Failure probability: "
            f"{result['failure_probability']:.2%}"
        )

        print(
            f"Unfinished probability: "
            f"{result['unfinished_probability']:.2%}"
        )

        print(
            f"Mean trades: "
            f"{result['mean_trades']:.2f}"
        )

        print(
            f"Mean trading profit: "
            f"${result['mean_profit']:,.2f}"
        )

        print(
            f"Median sessions to pass: "
            f"{days_label}"
        )

        print(
            f"Expected fees per pass: "
            f"${result['expected_pass_cost']:,.2f}"
        )

        print(
            f"Failure reasons: "
            f"{result['failures']}"
        )

        print(
            f"Runtime: "
            f"{result['runtime']:.3f} s"
        )

    best = max(
        results,

        key=lambda result:
            result["pass_probability"]
    )

    print("\n" + "=" * 72)

    print("HIGHEST OBSERVED PASS PROBABILITY")

    print(
        f"Risk per trade: "
        f"${best['risk']}"
    )

    print(
        f"Pass probability: "
        f"{best['pass_probability']:.2%}"
    )

    print(
        f"Expected evaluation fees per pass: "
        f"${best['expected_pass_cost']:,.2f}"
    )


if __name__ == "__main__":

    main()