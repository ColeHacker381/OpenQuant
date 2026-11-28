from dataclasses import dataclass, field
from datetime import datetime

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
        balance_cents=starting_balance
    )

