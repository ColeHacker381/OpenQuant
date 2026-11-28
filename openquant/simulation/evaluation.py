from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from ..propfirm.ruleset import EvaluationRules

from ..propfirm.validation import SimulationMode

from .account import AccountState

from .enums import (
    AccountStage,
    AccountStatus,
    EvaluationOutcome,
    TerminationReason
)

from .events import (
    AccountEvent,
    SessionStarted,
    SessionEnded
)

from .money import dollars_to_cents

from .consistency import (
    ConsistencyResult,
    compile_consistency,
    evaluate_consistency
)


class EvaluationConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledEvaluation:

    profit_target_cents: int

    minimum_trading_days: int

    maximum_trading_days: int | None

    maximum_calendar_days: int | None

    consistency_ratio: Fraction | None

def compile_evaluation(
    rules: EvaluationRules
) -> CompiledEvaluation:

    if not isinstance(rules, EvaluationRules):

        raise EvaluationConfigurationError(
            "Expected EvaluationRules"
        )

    try:

        target = dollars_to_cents(
            rules.profit_target
        )

    except (TypeError, ValueError) as exc:

        raise EvaluationConfigurationError(
            str(exc)
        ) from exc

    if target <= 0:

        raise EvaluationConfigurationError(
            "Profit target must be positive"
        )

    minimum = rules.minimum_trading_days

    if type(minimum) is not int or minimum < 0:

        raise EvaluationConfigurationError(
            "Invalid minimum trading days"
        )

    for maximum in (
        rules.maximum_trading_days,
        rules.maximum_calendar_days
    ):

        if maximum is None:
            continue

        if type(maximum) is not int or maximum <= 0:

            raise EvaluationConfigurationError(
                "Maximum days must be positive integers"
            )

        if maximum < minimum:

            raise EvaluationConfigurationError(
                "Maximum days cannot be below minimum days"
            )

    ratio = (
        None
        if rules.consistency is None
        else compile_consistency(rules.consistency)
    )

    return CompiledEvaluation(
        profit_target_cents=target,
        minimum_trading_days=minimum,
        maximum_trading_days=rules.maximum_trading_days,
        maximum_calendar_days=rules.maximum_calendar_days,
        consistency_ratio=ratio
    )

@dataclass(frozen=True)
class EvaluationProgress:

    net_profit_cents: int

    profit_target_cents: int

    trading_days: int

    minimum_trading_days: int

    consistency: ConsistencyResult | None

    @property
    def target_satisfied(self) -> bool:

        return (
            self.net_profit_cents
            >= self.profit_target_cents
        )

    @property
    def days_satisfied(self) -> bool:

        return (
            self.trading_days
            >= self.minimum_trading_days
        )

    @property
    def consistency_satisfied(self) -> bool:

        if self.consistency is None:
            return True

        return self.consistency.satisfied

    @property
    def effective_target_cents(self) -> int:

        if self.consistency is None:

            return self.profit_target_cents

        return max(
            self.profit_target_cents,
            self.consistency.required_profit_cents
        )

    @property
    def additional_profit_cents(self) -> int:

        return max(
            0,
            self.effective_target_cents
            - self.net_profit_cents
        )

    @property
    def remaining_trading_days(self) -> int:

        return max(
            0,
            self.minimum_trading_days
            - self.trading_days
        )

    @property
    def eligible(self) -> bool:

        return (
            self.target_satisfied
            and self.days_satisfied
            and self.consistency_satisfied
        )

def get_evaluation_progress(
    account: AccountState,
    rules: CompiledEvaluation
) -> EvaluationProgress:

    if account.stage is not AccountStage.EVALUATION:

        raise ValueError(
            "Account is not in evaluation"
        )

    total_profit = (
        account.completed_session_pnl_cents
    )

    consistency = None

    if rules.consistency_ratio is not None:

        consistency = evaluate_consistency(
            total_profit_cents=total_profit,

            best_day_cents=(
                account.best_day_pnl_cents
            ),

            maximum_ratio=(
                rules.consistency_ratio
            )
        )

    return EvaluationProgress(
        net_profit_cents=total_profit,

        profit_target_cents=(
            rules.profit_target_cents
        ),

        trading_days=account.trading_days,

        minimum_trading_days=(
            rules.minimum_trading_days
        ),

        consistency=consistency
    )

def evaluation_calendar_days(
    account: AccountState,
    timestamp: datetime
) -> int:

    started = account.evaluation_started_at

    if started is None:
        return 0

    elapsed = (
        timestamp.date()
        - started.date()
    ).days

    if elapsed < 0:

        raise RuntimeError(
            "Timestamp precedes evaluation start"
        )

    # Starting date counts as day one.
    return elapsed + 1

def expire_evaluation(
    account: AccountState,
    timestamp: datetime
) -> None:

    account.status = AccountStatus.TERMINATED

    account.termination_reason = (
        TerminationReason.EXPIRATION
    )

    account.evaluation_outcome = (
        EvaluationOutcome.FAILED
    )

    account.evaluation_completed_at = timestamp

def pass_evaluation(
    account: AccountState,
    timestamp: datetime
) -> None:

    account.evaluation_outcome = (
        EvaluationOutcome.PASSED
    )

    account.evaluation_completed_at = timestamp

    if (
        account.definition.simulation_mode
        is SimulationMode.EVALUATION_ONLY
    ):

        account.status = AccountStatus.CLOSED

        account.pending_stage = None

    else:

        if account.revision.firm.ruleset.funded is None:

            raise RuntimeError(
                "Funded configuration is missing"
            )

        account.status = (
            AccountStatus.AWAITING_ACTIVATION
        )

        account.pending_stage = (
            AccountStage.FUNDED
        )

def process_evaluation(
    account: AccountState,
    rules: CompiledEvaluation,
    event: AccountEvent
) -> None:

    if account.stage is not AccountStage.EVALUATION:

        return

    if (
        account.evaluation_outcome
        is not EvaluationOutcome.IN_PROGRESS
    ):

        return

    # Record the first accepted session start.

    if isinstance(event, SessionStarted):

        if account.evaluation_started_at is None:

            account.evaluation_started_at = (
                event.timestamp
            )

    # Financial risk violations take priority.

    if account.status is AccountStatus.TERMINATED:

        account.evaluation_outcome = (
            EvaluationOutcome.FAILED
        )

        account.evaluation_completed_at = (
            event.timestamp
        )

        return

    if not account.is_active:

        return

    # Calendar expiration is checked when a
    # new trading session begins.

    if isinstance(event, SessionStarted):

        maximum = rules.maximum_calendar_days

        if maximum is not None:

            elapsed = evaluation_calendar_days(
                account,
                event.timestamp
            )

            if elapsed > maximum:

                expire_evaluation(
                    account,
                    event.timestamp
                )

        return

    # Passing conditions are checked only
    # after a completed session.

    if not isinstance(event, SessionEnded):

        return

    progress = get_evaluation_progress(
        account,
        rules
    )

    flat_confirmed = (
        event.positions_flat is True
        and account.unrealized_pnl_cents == 0
    )

    if progress.eligible and flat_confirmed:

        pass_evaluation(
            account,
            event.timestamp
        )

        return

    # The final permitted trading day has
    # ended without a successful evaluation.

    maximum = rules.maximum_trading_days

    if (
        maximum is not None
        and progress.trading_days >= maximum
    ):

        expire_evaluation(
            account,
            event.timestamp
        )

