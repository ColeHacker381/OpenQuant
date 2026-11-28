from dataclasses import dataclass, field
from datetime import datetime

from .enums import EvaluationOutcome
from ..propfirm.revision import FirmRevision

from ..propfirm.ruleset import (
    EvaluationRules,
    FundedRules,
    LiveRules
)

from ..propfirm.validation import SimulationMode

from .enums import (
    AccountStage,
    AccountStatus,
    TerminationReason,
    CashFlowType
)

from .money import (
    dollars_to_cents,
    format_money
)

from .events import require_cents

@dataclass(frozen=True)
class AccountDefinition:

    account_id: str

    revision: FirmRevision

    simulation_mode: SimulationMode

    @property
    def nominal_size_cents(self) -> int:

        return dollars_to_cents(
            self.revision.firm.ruleset.account_size
        )

@dataclass(frozen=True)
class SessionRecord:

    session_id: str

    starting_balance_cents: int

    ending_balance_cents: int

    net_trading_pnl_cents: int

    trade_count: int

    @property
    def was_traded(self) -> bool:

        return self.trade_count > 0

    @property
    def was_profitable(self) -> bool:

        return self.net_trading_pnl_cents > 0

@dataclass(frozen=True)
class CashFlow:

    timestamp: datetime

    type: CashFlowType

    amount_cents: int

    def __post_init__(self):

        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):

            raise ValueError(
                "Cash flow requires a timezone-aware timestamp"
            )

        if not isinstance(self.type, CashFlowType):

            raise TypeError(
                "Invalid cash flow type"
            )

        require_cents(
            self.amount_cents,
            "Cash flow amount"
        )

        expense_types = (
            CashFlowType.PURCHASE,
            CashFlowType.ACTIVATION,
            CashFlowType.RECURRING_FEE,
            CashFlowType.RESET
        )

        if self.type in expense_types:

            if self.amount_cents > 0:

                raise ValueError(
                    "Expense cash flows must be nonpositive"
                )

        elif self.amount_cents < 0:

            raise ValueError(
                "Revenue cash flows must be nonnegative"
            )

@dataclass(frozen=True)
class PayoutRecord:

    timestamp: datetime

    stage: AccountStage

    gross_amount_cents: int

    trader_amount_cents: int

    balance_before_cents: int

    balance_after_cents: int

    @property
    def firm_amount_cents(self) -> int:

        return (
            self.gross_amount_cents
            - self.trader_amount_cents
        )

@dataclass
class AccountState:

    definition: AccountDefinition

    stage: AccountStage

    status: AccountStatus

    starting_balance_cents: int

    balance_cents: int

    unrealized_pnl_cents: int = 0

    # Trading performance for the current stage
    realized_pnl_cents: int = 0

    commissions_cents: int = 0

    trade_count: int = 0

    # Drawdown state
    high_water_mark_cents: int | None = None

    drawdown_floor_cents: int | None = None

    # Active session state
    session_id: str | None = None

    session_start_balance_cents: int | None = None

    session_start_equity_cents: int | None = None

    session_realized_pnl_cents: int = 0

    session_trade_count: int = 0

    session_locked: bool = False

    # Payout state
    payout_count: int = 0

    gross_withdrawals_cents: int = 0

    lifetime_payout_count: int = 0

    lifetime_gross_withdrawals_cents: int = 0

    # Lifecycle state
    pending_stage: AccountStage | None = None

    termination_reason: TerminationReason | None = None

    last_event_timestamp: datetime | None = None

    # Independent runtime histories
    session_history: list[SessionRecord] = field(
        default_factory=list
    )

    cash_flows: list[CashFlow] = field(
        default_factory=list
    )

    # Evaluation lifecycle

    evaluation_outcome: EvaluationOutcome | None = None

    evaluation_started_at: datetime | None = None

    evaluation_completed_at: datetime | None = None

    # Payout cycle state

    payout_cycle_start_index: int = 0

    last_payout_timestamp: datetime | None = None

    payout_history: list[PayoutRecord] = field(
        default_factory=list
    )

    @property
    def account_id(self) -> str:

        return self.definition.account_id

    @property
    def revision(self) -> FirmRevision:

        return self.definition.revision

    @property
    def equity_cents(self) -> int:

        return (
            self.balance_cents
            + self.unrealized_pnl_cents
        )

    @property
    def balance_profit_cents(self) -> int:

        return (
            self.balance_cents
            - self.starting_balance_cents
        )

    @property
    def is_active(self) -> bool:

        return self.status is AccountStatus.ACTIVE

    @property
    def has_active_session(self) -> bool:

        return self.session_id is not None

    @property
    def net_cash_flow_cents(self) -> int:

        return sum(
            flow.amount_cents
            for flow in self.cash_flows
        )

    @property
    def total_cash_expenses_cents(self) -> int:

        return -sum(
            flow.amount_cents

            for flow in self.cash_flows

            if flow.amount_cents < 0
        )

    @property
    def total_cash_received_cents(self) -> int:

        return sum(
            flow.amount_cents

            for flow in self.cash_flows

            if flow.amount_cents > 0
        )


    @property
    def stage_rules(
        self
    ) -> EvaluationRules | FundedRules | LiveRules:

        ruleset = self.revision.firm.ruleset

        if self.stage is AccountStage.EVALUATION:

            rules = ruleset.evaluation

        elif self.stage is AccountStage.FUNDED:

            rules = ruleset.funded

        elif self.stage is AccountStage.LIVE:

            rules = ruleset.live

        else:

            raise RuntimeError(
                "Invalid account stage"
            )

        if rules is None:

            raise RuntimeError(
                f"Missing configuration for {self.stage.value}"
            )

        return rules

    def __str__(self) -> str:

        return (
            f"{self.account_id} | "
            f"{self.stage.value} | "
            f"{self.status.value} | "
            f"Balance: {format_money(self.balance_cents)} | "
            f"Equity: {format_money(self.equity_cents)}"
        )

    @property
    def completed_sessions(self) -> int:

        return len(self.session_history)


    @property
    def trading_days(self) -> int:

        return sum(
            record.was_traded

            for record in self.session_history
        )


    @property
    def profitable_days(self) -> int:

        return sum(
            record.was_profitable

            for record in self.session_history
        )


    @property
    def best_day_pnl_cents(self) -> int:

        return max(
            (
                record.net_trading_pnl_cents

                for record in self.session_history

                if record.net_trading_pnl_cents > 0
            ),

            default=0
        )


    @property
    def gross_realized_pnl_cents(self) -> int:

        return (
            self.realized_pnl_cents
            + self.commissions_cents
        )


    @property
    def completed_session_pnl_cents(self) -> int:

        return sum(
            record.net_trading_pnl_cents

            for record in self.session_history
        )

    @property
    def drawdown_reference_cents(self) -> int:

        from ..propfirm.ruleset import BalanceReference

        rules = self.stage_rules.drawdown

        if rules is None:

            raise RuntimeError(
                "Account has no configured drawdown"
            )

        if rules.breach_reference is BalanceReference.BALANCE:

            return self.balance_cents

        if rules.breach_reference is BalanceReference.EQUITY:

            return self.equity_cents

        raise RuntimeError(
            "Invalid drawdown reference"
        )


    @property
    def remaining_drawdown_cents(self) -> int | None:

        if self.drawdown_floor_cents is None:

            return None

        return (
            self.drawdown_reference_cents
            - self.drawdown_floor_cents
        )


    @property
    def current_drawdown_cents(self) -> int | None:

        if self.high_water_mark_cents is None:

            return None

        return max(
            0,
            self.high_water_mark_cents
            - self.drawdown_reference_cents
        )

    @property
    def daily_loss_floor_cents(self) -> int | None:

        rules = self.stage_rules.daily_loss

        if rules is None or not self.has_active_session:

            return None

        from ..propfirm.ruleset import BalanceReference

        if rules.reference is BalanceReference.BALANCE:

            start = self.session_start_balance_cents

        elif rules.reference is BalanceReference.EQUITY:

            start = self.session_start_equity_cents

        else:

            raise RuntimeError(
                "Invalid daily loss reference"
            )

        return start - dollars_to_cents(
            rules.amount
        )


    @property
    def remaining_daily_loss_cents(self) -> int | None:

        floor = self.daily_loss_floor_cents

        if floor is None:

            return None

        from ..propfirm.ruleset import BalanceReference

        reference = self.stage_rules.daily_loss.reference

        if reference is BalanceReference.BALANCE:

            current = self.balance_cents

        else:

            current = self.equity_cents

        return current - floor

    @property
    def effective_loss_distance_cents(self) -> int | None:

        distances = [
            value

            for value in (
                self.remaining_drawdown_cents,
                self.remaining_daily_loss_cents
            )

            if value is not None
        ]

        if not distances:

            return None

        return min(distances)

    @property
    def payout_cycle_sessions(
        self
    ) -> tuple[SessionRecord, ...]:

        return tuple(
            self.session_history[
                self.payout_cycle_start_index:
            ]
        )

    @property
    def payout_cycle_profit_cents(self) -> int:

        return sum(
            record.net_trading_pnl_cents
            for record in self.payout_cycle_sessions
        )

    @property
    def lifetime_trader_payouts_cents(self) -> int:

        return sum(
            record.trader_amount_cents
            for record in self.payout_history
        )

class AccountCreationError(ValueError):
    pass


def create_account(
    account_id: str,
    revision: FirmRevision,
    mode: SimulationMode = SimulationMode.FULL_LIFECYCLE,
    *,
    require_verified: bool = True
) -> AccountState:

    if (
        type(account_id) is not str
        or not account_id.strip()
    ):

        raise AccountCreationError(
            "Account ID cannot be empty"
        )

    if not isinstance(revision, FirmRevision):

        raise TypeError(
            "Expected a FirmRevision instance"
        )

    if not isinstance(mode, SimulationMode):

        raise AccountCreationError(
            "Invalid simulation mode"
        )

    result = revision.validate_for_simulation(
        mode,
        require_verified=require_verified
    )

    if not result.is_valid:

        raise AccountCreationError(
            str(result)
        )

    ruleset = revision.firm.ruleset

    # Select initial stage
    if mode is SimulationMode.EVALUATION_ONLY:

        stage = AccountStage.EVALUATION

    elif mode is SimulationMode.FUNDED_ONLY:

        stage = AccountStage.FUNDED

    elif ruleset.evaluation is not None:

        stage = AccountStage.EVALUATION

    elif ruleset.funded is not None:

        stage = AccountStage.FUNDED

    elif ruleset.live is not None:

        stage = AccountStage.LIVE

    else:

        raise AccountCreationError(
            "No available account stage"
        )

    # Select starting balance
    if stage is AccountStage.LIVE:

        if ruleset.live.starting_balance is None:

            raise AccountCreationError(
                "Live starting balance is required"
            )

        starting_balance = dollars_to_cents(
            ruleset.live.starting_balance
        )

    else:

        starting_balance = dollars_to_cents(
            ruleset.account_size
        )

    definition = AccountDefinition(
        account_id=account_id.strip(),
        revision=revision,
        simulation_mode=mode
    )

    return AccountState(
        definition=definition,
        stage=stage,
        status=AccountStatus.ACTIVE,
        starting_balance_cents=starting_balance,
        balance_cents=starting_balance,

        evaluation_outcome=(
            EvaluationOutcome.IN_PROGRESS
            if stage is AccountStage.EVALUATION
            else None
        )
    )

