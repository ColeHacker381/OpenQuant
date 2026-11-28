from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from copy import deepcopy

from datetime import time

from .evaluation import expire_evaluation

from ..propfirm.revision import FirmRevision

from ..propfirm.ruleset import (
    FeeRules,
    LiveTransitionRules,
    TransitionMode,
    TransitionType
)

from ..propfirm.validation import SimulationMode

from .account import (
    AccountState,
    StageRecord,
    CashFlow,
    create_account
)

from .accounting import (
    assert_accounting_invariants,
    audit_payout_accounting,
    record_cash_flow
)

from .enums import (
    AccountStage,
    AccountStatus,
    CashFlowType,
    EvaluationOutcome,
    TerminationReason
)

from .events import (
    AccountEvent,
    SessionEnded,
    PayoutRequested,
    StageActivationRequested
)

from .engine import AccountEngine

from .money import dollars_to_cents


class LifecycleError(ValueError):
    pass


class LifecycleConfigurationError(LifecycleError):
    pass

@dataclass(frozen=True)
class CompiledFees:

    purchase_cents: int

    activation_cents: int

    recurring_cents: int

    recurring_interval_days: int | None

    reset_cents: int | None

def compile_fees(
    rules: FeeRules
) -> CompiledFees:

    if not isinstance(rules, FeeRules):

        raise LifecycleConfigurationError(
            "Expected FeeRules"
        )

    try:

        compiled = CompiledFees(
            purchase_cents=dollars_to_cents(
                rules.purchase
            ),

            activation_cents=dollars_to_cents(
                rules.activation
            ),

            recurring_cents=dollars_to_cents(
                rules.recurring
            ),

            recurring_interval_days=(
                rules.recurring_interval_days
            ),

            reset_cents=(
                None
                if rules.reset is None
                else dollars_to_cents(rules.reset)
            )
        )

    except (TypeError, ValueError) as exc:

        raise LifecycleConfigurationError(
            str(exc)
        ) from exc

    amounts = (
        compiled.purchase_cents,
        compiled.activation_cents,
        compiled.recurring_cents,
        compiled.reset_cents
    )

    if any(
        amount is not None and amount < 0
        for amount in amounts
    ):

        raise LifecycleConfigurationError(
            "Fees cannot be negative"
        )

    interval = compiled.recurring_interval_days

    if interval is not None and (
        type(interval) is not int
        or interval <= 0
    ):

        raise LifecycleConfigurationError(
            "Invalid recurring fee interval"
        )

    if (
        compiled.recurring_cents > 0
        and interval is None
    ):

        raise LifecycleConfigurationError(
            "Recurring fees require a billing interval"
        )

    return compiled

def archive_stage(
    account: AccountState,
    timestamp: datetime
) -> StageRecord:

    return StageRecord(
        stage=account.stage,

        status=account.status,

        started_at=account.stage_started_at,

        ended_at=timestamp,

        starting_balance_cents=(
            account.starting_balance_cents
        ),

        ending_balance_cents=(
            account.balance_cents
        ),

        ending_equity_cents=(
            account.equity_cents
        ),

        realized_pnl_cents=(
            account.realized_pnl_cents
        ),

        commissions_cents=(
            account.commissions_cents
        ),

        trade_count=account.trade_count,

        payout_count=account.payout_count,

        gross_withdrawals_cents=(
            account.gross_withdrawals_cents
        ),

        termination_reason=(
            account.termination_reason
        ),

        evaluation_outcome=(
            account.evaluation_outcome

            if account.stage is AccountStage.EVALUATION

            else None
        ),

        sessions=tuple(account.session_history)
    )

def build_stage_state(
    account: AccountState,
    stage: AccountStage,
    timestamp: datetime
) -> AccountState:

    ruleset = account.revision.firm.ruleset

    if stage is AccountStage.EVALUATION:

        rules = ruleset.evaluation

    elif stage is AccountStage.FUNDED:

        rules = ruleset.funded

    elif stage is AccountStage.LIVE:

        rules = ruleset.live

    else:

        raise LifecycleError(
            "Unsupported account stage"
        )

    if rules is None:

        raise LifecycleError(
            f"Missing configuration for {stage.value}"
        )

    if stage is AccountStage.LIVE:

        if rules.starting_balance is None:

            raise LifecycleConfigurationError(
                "Live starting balance is required"
            )

        balance = dollars_to_cents(
            rules.starting_balance
        )

    else:

        balance = dollars_to_cents(
            ruleset.account_size
        )

    history = [
        *account.stage_history,

        archive_stage(account, timestamp)
    ]

    evaluation_outcome = (
        account.evaluation_outcome
    )

    evaluation_started_at = (
        account.evaluation_started_at
    )

    evaluation_completed_at = (
        account.evaluation_completed_at
    )

    if stage is AccountStage.EVALUATION:

        evaluation_outcome = (
            EvaluationOutcome.IN_PROGRESS
        )

        evaluation_started_at = timestamp

        evaluation_completed_at = None

    return AccountState(
        definition=account.definition,

        stage=stage,

        status=AccountStatus.ACTIVE,

        starting_balance_cents=balance,

        balance_cents=balance,

        evaluation_outcome=evaluation_outcome,

        evaluation_started_at=evaluation_started_at,

        evaluation_completed_at=evaluation_completed_at,

        purchased_at=account.purchased_at,

        stage_started_at=timestamp,

        last_billing_timestamp=timestamp,

        last_event_timestamp=None,

        lifetime_payout_count=(
            account.lifetime_payout_count
        ),

        lifetime_gross_withdrawals_cents=(
            account.lifetime_gross_withdrawals_cents
        ),

        reset_count=account.reset_count,

        stage_history=history,

        payout_history=list(
            account.payout_history
        ),

        cash_flows=list(
            account.cash_flows
        )
    )

@dataclass(frozen=True)
class LiveTransitionProgress:

    conditions: tuple[tuple[str, bool], ...]

    mode: TransitionMode

    manual_review: bool

    @property
    def eligible(self) -> bool:

        if not self.conditions:

            return False

        results = (
            satisfied
            for _, satisfied in self.conditions
        )

        if self.mode is TransitionMode.ALL:

            return all(results)

        if self.mode is TransitionMode.ANY:

            return any(results)

        raise LifecycleConfigurationError(
            "Unsupported transition mode"
        )

    @property
    def automatic_transition_allowed(self) -> bool:

        return (
            self.eligible
            and not self.manual_review
        )

def get_live_transition_progress(
    account: AccountState,
    rules: LiveTransitionRules
) -> LiveTransitionProgress:

    if account.stage is not AccountStage.FUNDED:

        raise LifecycleError(
            "Live qualification requires a funded account"
        )

    conditions = []

    if rules.minimum_payout_count is not None:

        conditions.append((
            "minimum_payout_count",

            account.payout_count
            >= rules.minimum_payout_count
        ))

    if rules.minimum_total_payouts is not None:

        conditions.append((
            "minimum_total_payouts",

            account.gross_withdrawals_cents
            >= dollars_to_cents(
                rules.minimum_total_payouts
            )
        ))

    if rules.minimum_trading_days is not None:

        conditions.append((
            "minimum_trading_days",

            account.trading_days
            >= rules.minimum_trading_days
        ))

    if rules.minimum_profit is not None:

        conditions.append((
            "minimum_profit",

            account.completed_session_pnl_cents
            >= dollars_to_cents(
                rules.minimum_profit
            )
        ))

    return LiveTransitionProgress(
        conditions=tuple(conditions),

        mode=rules.mode,

        manual_review=(
            rules.transition_type
            is TransitionType.MANUAL_REVIEW
        )
    )

def normalize_timestamp(
    timestamp: datetime
) -> datetime:

    if not isinstance(timestamp, datetime):

        raise TypeError(
            "Timestamp must be a datetime"
        )

    if (
        timestamp.tzinfo is None
        or timestamp.utcoffset() is None
    ):

        raise LifecycleError(
            "Timestamp must be timezone-aware"
        )

    return timestamp.astimezone(timezone.utc)

class AccountLifecycle:

    def __init__(
        self,
        engine: AccountEngine,
        clock: datetime
    ):

        self.engine = engine

        self.clock = normalize_timestamp(clock)

        self.fees = compile_fees(
            engine.account.stage_rules.fees
        )

    @property
    def account(self) -> AccountState:

        return self.engine.account

    @property
    def strict(self) -> bool:

        return self.engine.strict

    @classmethod
    def purchase(
        cls,
        account_id: str,
        revision: FirmRevision,
        timestamp: datetime,

        mode: SimulationMode = (
            SimulationMode.FULL_LIFECYCLE
        ),

        *,
        require_verified: bool = True,
        strict: bool = True

    ) -> "AccountLifecycle":

        timestamp = normalize_timestamp(
            timestamp
        )

        account = create_account(
            account_id,
            revision,
            mode,
            require_verified=require_verified
        )

        engine = AccountEngine(
            account,
            strict=strict
        )

        lifecycle = cls(
            engine,
            timestamp
        )

        account.purchased_at = timestamp

        account.stage_started_at = timestamp

        account.last_billing_timestamp = timestamp

        account.last_event_timestamp = timestamp

        if account.stage is AccountStage.EVALUATION:

            account.evaluation_started_at = timestamp

        lifecycle._record_fee(
            CashFlowType.PURCHASE,
            lifecycle.fees.purchase_cents,
            timestamp
        )

        # Direct funded and live accounts start
        # immediately, so activation fees apply.

        if account.stage is not AccountStage.EVALUATION:

            lifecycle._record_fee(
                CashFlowType.ACTIVATION,
                lifecycle.fees.activation_cents,
                timestamp
            )

        return lifecycle

    def _record_fee(
        self,
        fee_type: CashFlowType,
        amount_cents: int,
        timestamp: datetime
    ) -> None:

        if amount_cents == 0:

            return

        record_cash_flow(
            self.account,

            CashFlow(
                timestamp=timestamp,

                type=fee_type,

                amount_cents=-amount_cents
            )
        )

    def _validate_timestamp(
        self,
        timestamp: datetime
    ) -> datetime:

        timestamp = normalize_timestamp(
            timestamp
        )

        if timestamp < self.clock:

            raise LifecycleError(
                "Timestamp precedes lifecycle clock"
            )

        return timestamp

    def _evaluation_deadline(
        self
    ) -> datetime | None:

        account = self.account

        if (
            account.stage is not AccountStage.EVALUATION
            or not account.is_active
        ):

            return None

        maximum = (
            account.stage_rules.maximum_calendar_days
        )

        if maximum is None:

            return None

        started = account.evaluation_started_at

        if started is None:

            raise LifecycleError(
                "Evaluation starting timestamp is missing"
            )

        deadline_date = (
            started.date()
            + timedelta(days=maximum)
        )

        return datetime.combine(
            deadline_date,
            time.min,
            tzinfo=timezone.utc
        )

    def advance_time(
        self,
        timestamp: datetime
    ) -> int:

        timestamp = self._validate_timestamp(
            timestamp
        )

        account = self.account

        deadline = self._evaluation_deadline()

        expires = (
            deadline is not None
            and deadline <= timestamp
        )

        billing_end = (
            deadline
            if expires
            else timestamp
        )

        billed = 0

        if (
            account.is_active
            and self.fees.recurring_cents > 0
        ):

            interval = timedelta(
                days=self.fees.recurring_interval_days
            )

            last_billing = (
                account.last_billing_timestamp
            )

            if last_billing is None:

                raise LifecycleError(
                    "Billing timestamp is missing"
                )

            due = last_billing + interval

            while due <= billing_end:

                # Expiration wins if billing and
                # expiration occur simultaneously.

                if expires and due == deadline:

                    break

                self._record_fee(
                    CashFlowType.RECURRING_FEE,

                    self.fees.recurring_cents,

                    due
                )

                account.last_billing_timestamp = due

                billed += 1

                due += interval

        if expires:

            expire_evaluation(
                account,
                deadline
            )

            account.last_event_timestamp = deadline

        self.clock = timestamp

        return billed

    def _replace_stage(
        self,
        stage: AccountStage,
        timestamp: datetime,
        *,
        reset: bool = False
    ) -> None:

        # Build a candidate without modifying
        # the existing account or engine.

        candidate = build_stage_state(
            self.account,
            stage,
            timestamp
        )

        fees = compile_fees(
            candidate.stage_rules.fees
        )

        candidate_engine = AccountEngine(
            candidate,
            strict=self.strict
        )

        candidate.last_event_timestamp = timestamp

        if reset:

            reset_fee = fees.reset_cents

            if reset_fee is None:

                raise LifecycleError(
                    "Account resets are unavailable"
                )

            candidate.reset_count += 1

            charges = (
                (CashFlowType.RESET, reset_fee),
            )

        else:

            charges = (
                (
                    CashFlowType.PURCHASE,
                    fees.purchase_cents
                ),

                (
                    CashFlowType.ACTIVATION,
                    fees.activation_cents
                )
            )

        for fee_type, amount in charges:

            if amount > 0:

                record_cash_flow(
                    candidate,

                    CashFlow(
                        timestamp,
                        fee_type,
                        -amount
                    )
                )

        # Validate before committing the candidate.

        assert_accounting_invariants(candidate)

        audit_payout_accounting(candidate)

        self.engine = candidate_engine

        self.fees = fees

        self.clock = timestamp

    def activate(
        self,
        timestamp: datetime
    ) -> None:

        timestamp = self._validate_timestamp(
            timestamp
        )

        account = self.account

        if (
            account.status
            is not AccountStatus.AWAITING_ACTIVATION
        ):

            raise LifecycleError(
                "Account is not awaiting activation"
            )

        stage = account.pending_stage

        expected_transitions = {
            AccountStage.EVALUATION: AccountStage.FUNDED,

            AccountStage.FUNDED: AccountStage.LIVE
        }

        if (
            stage is None

            or expected_transitions.get(account.stage)
            is not stage
        ):

            raise LifecycleError(
                "Invalid pending stage transition"
            )

        if (
            account.has_active_session
            or account.unrealized_pnl_cents != 0
        ):

            raise LifecycleError(
                "Cannot activate with unfinished trading"
            )

        self._replace_stage(
            stage,
            timestamp
        )

    @property
    def live_transition_progress(
        self
    ) -> LiveTransitionProgress | None:

        account = self.account

        if account.stage is not AccountStage.FUNDED:

            return None

        rules = account.stage_rules.live_transition

        if rules is None:

            return None

        return get_live_transition_progress(
            account,
            rules
        )

    def _check_live_transition(
        self,
        event: AccountEvent
    ) -> None:

        account = self.account

        if not account.is_active:

            return

        if account.stage is not AccountStage.FUNDED:

            return

        if (
            account.definition.simulation_mode
            is not SimulationMode.FULL_LIFECYCLE
        ):

            return

        # Only qualify at a confirmed flat,
        # completed-session boundary.

        if not isinstance(
            event,
            (SessionEnded, PayoutRequested)
        ):

            return

        if (
            event.positions_flat is not True
            or account.has_active_session
            or account.unrealized_pnl_cents != 0
        ):

            return

        progress = self.live_transition_progress

        if progress is None:

            return

        if not progress.automatic_transition_allowed:

            return

        if account.revision.firm.ruleset.live is None:

            raise LifecycleConfigurationError(
                "Live transition has no destination rules"
            )

        account.status = (
            AccountStatus.AWAITING_ACTIVATION
        )

        account.pending_stage = AccountStage.LIVE

    def reset(
        self,
        timestamp: datetime
    ) -> None:

        timestamp = self._validate_timestamp(
            timestamp
        )

        account = self.account

        if account.stage is not AccountStage.EVALUATION:

            raise LifecycleError(
                "Only evaluation resets are supported"
            )

        if (
            account.status
            is not AccountStatus.TERMINATED
        ):

            raise LifecycleError(
                "Only terminated evaluations can reset"
            )

        if (
            account.evaluation_outcome
            is not EvaluationOutcome.FAILED
        ):

            raise LifecycleError(
                "Evaluation has not failed"
            )

        if self.fees.reset_cents is None:

            raise LifecycleError(
                "Account resets are unavailable"
            )

        self._replace_stage(
            AccountStage.EVALUATION,

            timestamp,

            reset=True
        )

    def process(
        self,
        event: AccountEvent
    ) -> bool:

        if not isinstance(event, AccountEvent):

            raise TypeError(
                "Expected AccountEvent"
            )

        timestamp = self._validate_timestamp(
            event.timestamp
        )

        # Activation is managed here rather
        # than by the current stage engine.

        if isinstance(
            event,
            StageActivationRequested
        ):

            self.activate(timestamp)

            return True

        if not self.account.is_active:

            raise LifecycleError(
                "Account is not active"
            )

        # Time can advance independently
        # of financial event acceptance.

        self.advance_time(timestamp)

        # The evaluation may have expired
        # before this event could occur.

        if not self.account.is_active:

            return False

        self.engine.process(event)

        self._check_live_transition(event)

        return True

    def authorize_trade(
        self,
        request
    ):

        return self.engine.authorize_trade(
            request
        )


    def assess_payout(
        self,
        event: PayoutRequested
    ):

        self._validate_timestamp(
            event.timestamp
        )

        return self.engine.assess_payout(
            event
        )

    def result(self) -> "LifecycleResult":

        return get_lifecycle_result(self)

@dataclass(frozen=True)
class LifecycleResult:

    account_id: str

    stage: AccountStage

    status: AccountStatus

    evaluation_passed: bool

    reached_funded: bool

    reached_live: bool

    evaluation_attempts: int

    reset_count: int

    payout_count: int

    total_trades: int

    trading_pnl_cents: int

    expenses_cents: int

    cash_received_cents: int

    payout_revenue_cents: int

    net_cash_cents: int

    termination_reason: TerminationReason | None

    elapsed_days: float

    @property
    def profitable(self) -> bool:

        return self.net_cash_cents > 0

    @property
    def received_payout(self) -> bool:

        return self.payout_revenue_cents > 0

    @property
    def cash_return_ratio(self) -> float | None:

        if self.expenses_cents == 0:

            return None

        return (
            self.net_cash_cents
            / self.expenses_cents
        )

def get_lifecycle_result(
    lifecycle: AccountLifecycle
) -> LifecycleResult:

    account = lifecycle.account

    history = account.stage_history

    stages = {
        account.stage,

        *(
            record.stage
            for record in history
        )
    }

    evaluation_passed = (
        account.evaluation_outcome
        is EvaluationOutcome.PASSED

        or any(
            record.evaluation_outcome
            is EvaluationOutcome.PASSED

            for record in history
        )
    )

    evaluation_attempts = sum(
        record.stage is AccountStage.EVALUATION
        for record in history
    )

    if account.stage is AccountStage.EVALUATION:

        evaluation_attempts += 1

    elapsed = (
        lifecycle.clock
        - account.purchased_at
    )

    audit_payout_accounting(account)

    return LifecycleResult(
        account_id=account.account_id,

        stage=account.stage,

        status=account.status,

        evaluation_passed=evaluation_passed,

        reached_funded=(
            AccountStage.FUNDED in stages
        ),

        reached_live=(
            AccountStage.LIVE in stages
        ),

        evaluation_attempts=evaluation_attempts,

        reset_count=account.reset_count,

        payout_count=account.lifetime_payout_count,

        total_trades=account.lifetime_trade_count,

        trading_pnl_cents=(
            account.lifetime_realized_pnl_cents
        ),

        expenses_cents=(
            account.total_cash_expenses_cents
        ),

        cash_received_cents=(
            account.total_cash_received_cents
        ),

        payout_revenue_cents=(
            account.lifetime_trader_payouts_cents
        ),

        net_cash_cents=account.net_cash_flow_cents,

        termination_reason=account.termination_reason,

        elapsed_days=(
            elapsed.total_seconds() / 86_400
        )
    )

