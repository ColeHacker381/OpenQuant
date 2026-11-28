import pytest

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    EvaluationRules,
    FundedRules,
    LiveRules,
    LiveTransitionRules,
    TransitionMode,
    TransitionType,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    PayoutRules,
    ConsistencyRules,
    ConsistencyWindow
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.propfirm.validation import SimulationMode

from openquant.simulation.lifecycle import (
    AccountLifecycle,
    LifecycleError,
    LifecycleConfigurationError,
    compile_fees,
    get_live_transition_progress
)

from openquant.simulation.accounting import (
    audit_payout_accounting
)

from openquant.simulation.enums import (
    AccountStage,
    AccountStatus,
    CashFlowType,
    EvaluationOutcome,
    TerminationReason
)

from openquant.simulation.events import (
    SessionStarted,
    SessionEnded,
    TradeRealized,
    PayoutRequested,
    StageActivationRequested
)

from openquant.simulation.drawdown import (
    DrawdownConfigurationError
)

from openquant.simulation.payout import (
    check_payout_consistency
)


# ==================================================
# Fixtures and helpers
# ==================================================

@pytest.fixture
def timestamp():

    return datetime(
        2026, 1, 5, 14, 30,
        tzinfo=timezone.utc
    )


@pytest.fixture
def revision():

    drawdown = DrawdownRules(
        DrawdownType.STATIC,
        1000
    )

    return FirmRevision(
        firm=PropFirm(
            "Example Trading",

            Ruleset(
                account_name="25K",

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
        ),

        metadata=RevisionMetadata()
    )


def modify_stage(revision, stage, **kwargs):

    ruleset = revision.firm.ruleset

    rules = getattr(ruleset, stage)

    updated_rules = replace(
        rules,
        **kwargs
    )

    return replace(
        revision,

        firm=replace(
            revision.firm,

            ruleset=replace(
                ruleset,

                **{stage: updated_rules}
            )
        )
    )


@pytest.fixture
def make_lifecycle(revision, timestamp):

    def factory(
        *,
        custom_revision=None,
        strict=True,
        mode=SimulationMode.FULL_LIFECYCLE
    ):

        return AccountLifecycle.purchase(
            "account_001",

            (
                revision
                if custom_revision is None
                else custom_revision
            ),

            timestamp,

            mode,

            require_verified=False,

            strict=strict
        )

    return factory


def trade_day(
    lifecycle,
    timestamp,
    pnl,
    *,
    flat=True
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
                pnl
            )
        )

    if lifecycle.account.is_active:

        lifecycle.process(
            SessionEnded(
                timestamp + timedelta(hours=1),
                session_id,
                positions_flat=flat
            )
        )


def pass_evaluation(lifecycle, timestamp):

    for day, pnl in enumerate(
        [50_000, 50_000, 25_000]
    ):

        trade_day(
            lifecycle,

            timestamp + timedelta(days=day),

            pnl
        )


def activate_funded(lifecycle, timestamp):

    pass_evaluation(
        lifecycle,
        timestamp
    )

    lifecycle.activate(
        timestamp + timedelta(days=3)
    )


def funded_payout(lifecycle, timestamp):

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


# ==================================================
# Fee compilation
# ==================================================

def test_compile_fees():

    fees = compile_fees(
        FeeRules(
            purchase=100,
            activation=50,
            recurring=25,
            recurring_interval_days=30,
            reset=75
        )
    )

    assert fees.purchase_cents == 10_000

    assert fees.activation_cents == 5000

    assert fees.recurring_cents == 2500

    assert fees.recurring_interval_days == 30

    assert fees.reset_cents == 7500


def test_unavailable_reset():

    fees = compile_fees(
        FeeRules(reset=None)
    )

    assert fees.reset_cents is None


def test_free_reset():

    fees = compile_fees(
        FeeRules(reset=0)
    )

    assert fees.reset_cents == 0


@pytest.mark.parametrize(
    "rules",
    [
        FeeRules(purchase=-100),
        FeeRules(activation=-50),
        FeeRules(reset=-75),
        FeeRules(recurring=25),
        FeeRules(recurring_interval_days=0),
        FeeRules(purchase=100.001)
    ]
)
def test_invalid_fees(rules):

    with pytest.raises(
        LifecycleConfigurationError
    ):

        compile_fees(rules)


# ==================================================
# Account purchase
# ==================================================

def test_evaluation_purchase(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    account = lifecycle.account

    assert account.purchased_at == timestamp

    assert account.stage_started_at == timestamp

    assert account.evaluation_started_at == timestamp

    assert account.last_billing_timestamp == timestamp

    assert account.net_cash_flow_cents == -10_000

    assert account.balance_cents == 2_500_000

    assert account.cash_flows[0].type is CashFlowType.PURCHASE


def test_funded_only_purchase(make_lifecycle):

    lifecycle = make_lifecycle(
        mode=SimulationMode.FUNDED_ONLY
    )

    assert (
        lifecycle.account.stage
        is AccountStage.FUNDED
    )

    assert lifecycle.account.net_cash_flow_cents == -5000


def test_purchase_and_activation_fees(
    make_lifecycle,
    revision
):

    updated = modify_stage(
        revision,

        "funded",

        fees=FeeRules(
            purchase=100,
            activation=50
        )
    )

    lifecycle = make_lifecycle(
        custom_revision=updated,

        mode=SimulationMode.FUNDED_ONLY
    )

    assert lifecycle.account.net_cash_flow_cents == -15_000


def test_naive_purchase_timestamp(
    revision
):

    with pytest.raises(LifecycleError):

        AccountLifecycle.purchase(
            "account_001",

            revision,

            datetime(2026, 1, 5),

            require_verified=False
        )


def test_clock_cannot_move_backwards(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    with pytest.raises(LifecycleError):

        lifecycle.advance_time(
            timestamp - timedelta(seconds=1)
        )


# ==================================================
# Funded activation
# ==================================================

def test_successful_evaluation_awaits_activation(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    pass_evaluation(lifecycle, timestamp)

    assert (
        lifecycle.account.status
        is AccountStatus.AWAITING_ACTIVATION
    )

    assert (
        lifecycle.account.pending_stage
        is AccountStage.FUNDED
    )

    assert lifecycle.account.net_cash_flow_cents == -10_000


def test_activate_funded(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    account = lifecycle.account

    assert account.stage is AccountStage.FUNDED

    assert account.is_active

    assert account.pending_stage is None

    assert account.balance_cents == 2_500_000

    assert account.starting_balance_cents == 2_500_000

    assert account.net_cash_flow_cents == -15_000

    assert lifecycle.engine.evaluation is None

    assert lifecycle.engine.payout is not None


def test_activation_via_event(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    pass_evaluation(lifecycle, timestamp)

    lifecycle.process(
        StageActivationRequested(
            timestamp + timedelta(days=3)
        )
    )

    assert (
        lifecycle.account.stage
        is AccountStage.FUNDED
    )


def test_evaluation_profit_not_transferred(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    account = lifecycle.account

    assert account.realized_pnl_cents == 0

    assert account.lifetime_realized_pnl_cents == 125_000

    assert account.trade_count == 0

    assert account.lifetime_trade_count == 3

    assert account.session_history == []


def test_funded_drawdown_reinitialized(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    account = lifecycle.account

    assert account.high_water_mark_cents == 2_500_000

    assert account.drawdown_floor_cents == 2_400_000


def test_evaluation_archive(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    history = lifecycle.account.stage_history

    assert len(history) == 1

    record = history[0]

    assert record.stage is AccountStage.EVALUATION

    assert (
        record.evaluation_outcome
        is EvaluationOutcome.PASSED
    )

    assert record.ending_balance_cents == 2_625_000

    assert record.realized_pnl_cents == 125_000

    assert record.trade_count == 3

    assert record.trading_days == 3


def test_pinned_revision_preserved(
    make_lifecycle,
    timestamp,
    revision
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    assert lifecycle.account.revision is revision

    assert lifecycle.account.account_id == "account_001"


def test_early_activation_rejected(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    before = deepcopy(lifecycle.account)

    with pytest.raises(LifecycleError):

        lifecycle.activate(timestamp)

    assert lifecycle.account == before


def test_duplicate_activation_rejected(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    before = deepcopy(lifecycle.account)

    with pytest.raises(LifecycleError):

        lifecycle.activate(
            timestamp + timedelta(days=4)
        )

    assert lifecycle.account == before


def test_failed_activation_preserves_stage(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "funded",

        drawdown=DrawdownRules(
            DrawdownType.TRAILING_EOD,

            1000,

            trailing_stop_balance=23_000
        )
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    pass_evaluation(lifecycle, timestamp)

    before = deepcopy(lifecycle.account)

    with pytest.raises(DrawdownConfigurationError):

        lifecycle.activate(
            timestamp + timedelta(days=3)
        )

    assert lifecycle.account == before

    assert lifecycle.account.net_cash_flow_cents == -10_000


# ==================================================
# Recurring fees
# ==================================================

@pytest.fixture
def recurring_revision(revision):

    return modify_stage(
        revision,

        "evaluation",

        fees=FeeRules(
            purchase=100,

            recurring=50,

            recurring_interval_days=30,

            reset=75
        )
    )


def test_no_fee_before_due(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    assert lifecycle.advance_time(
        timestamp + timedelta(days=29)
    ) == 0

    assert lifecycle.account.net_cash_flow_cents == -10_000


def test_exact_recurring_due_date(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    due = timestamp + timedelta(days=30)

    assert lifecycle.advance_time(due) == 1

    assert lifecycle.account.net_cash_flow_cents == -15_000

    assert lifecycle.account.cash_flows[-1].timestamp == due


def test_multiple_elapsed_billing_intervals(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    assert lifecycle.advance_time(
        timestamp + timedelta(days=95)
    ) == 3

    assert lifecycle.account.net_cash_flow_cents == -25_000

    dates = [
        flow.timestamp

        for flow in lifecycle.account.cash_flows

        if flow.type is CashFlowType.RECURRING_FEE
    ]

    assert dates == [
        timestamp + timedelta(days=30),
        timestamp + timedelta(days=60),
        timestamp + timedelta(days=90)
    ]


def test_billing_does_not_duplicate(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    due = timestamp + timedelta(days=30)

    lifecycle.advance_time(due)

    assert lifecycle.advance_time(due) == 0

    assert lifecycle.account.net_cash_flow_cents == -15_000


def test_trading_event_advances_billing(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    lifecycle.process(
        SessionStarted(
            timestamp + timedelta(days=31),
            "day_31"
        )
    )

    assert lifecycle.account.net_cash_flow_cents == -15_000


def test_terminated_account_not_billed(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    assert lifecycle.advance_time(
        timestamp + timedelta(days=90)
    ) == 0

    assert lifecycle.account.net_cash_flow_cents == -10_000


def test_pending_activation_not_billed(
    make_lifecycle,
    recurring_revision,
    timestamp
):

    lifecycle = make_lifecycle(
        custom_revision=recurring_revision
    )

    pass_evaluation(lifecycle, timestamp)

    assert lifecycle.advance_time(
        timestamp + timedelta(days=90)
    ) == 0

    assert lifecycle.account.net_cash_flow_cents == -10_000


# ==================================================
# Purchase-based expiration
# ==================================================

def test_evaluation_expires_without_trading(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "evaluation",

        maximum_calendar_days=3
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    lifecycle.advance_time(
        timestamp + timedelta(days=4)
    )

    assert (
        lifecycle.account.termination_reason
        is TerminationReason.EXPIRATION
    )

    assert (
        lifecycle.account.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert lifecycle.account.trade_count == 0


def test_expired_evaluation_cannot_trade(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "evaluation",

        maximum_calendar_days=3
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    processed = lifecycle.process(
        SessionStarted(
            timestamp + timedelta(days=3),
            "late_session"
        )
    )

    assert processed is False

    assert not lifecycle.account.has_active_session

    assert not lifecycle.account.is_active


def test_no_fees_after_expiration(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "evaluation",

        maximum_calendar_days=3,

        fees=FeeRules(
            purchase=100,
            recurring=50,
            recurring_interval_days=1
        )
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    lifecycle.advance_time(
        timestamp + timedelta(days=10)
    )

    # Jan 6 and Jan 7 are billable.
    # Expiration occurs Jan 8 at midnight UTC.

    assert lifecycle.account.net_cash_flow_cents == -20_000

    assert (
        lifecycle.account.termination_reason
        is TerminationReason.EXPIRATION
    )


# ==================================================
# Evaluation resets
# ==================================================

def test_failed_evaluation_reset(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    lifecycle.reset(
        timestamp + timedelta(days=1)
    )

    account = lifecycle.account

    assert account.is_active

    assert account.reset_count == 1

    assert account.balance_cents == 2_500_000

    assert account.drawdown_floor_cents == 2_400_000

    assert account.termination_reason is None

    assert account.net_cash_flow_cents == -17_500

    assert (
        account.evaluation_outcome
        is EvaluationOutcome.IN_PROGRESS
    )


def test_reset_preserves_failed_attempt(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    lifecycle.reset(
        timestamp + timedelta(days=1)
    )

    record = lifecycle.account.stage_history[0]

    assert (
        record.termination_reason
        is TerminationReason.DRAWDOWN
    )

    assert (
        record.evaluation_outcome
        is EvaluationOutcome.FAILED
    )

    assert record.realized_pnl_cents == -100_000

    assert record.trade_count == 1

    assert lifecycle.account.lifetime_trade_count == 1


def test_reset_restarts_evaluation_clock(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    reset_time = timestamp + timedelta(days=5)

    lifecycle.reset(reset_time)

    assert (
        lifecycle.account.evaluation_started_at
        == reset_time
    )

    assert lifecycle.account.purchased_at == timestamp

    assert (
        lifecycle.account.last_billing_timestamp
        == reset_time
    )


def test_reset_unavailable(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "evaluation",

        fees=FeeRules(
            purchase=100,
            reset=None
        )
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    before = deepcopy(lifecycle.account)

    with pytest.raises(LifecycleError):

        lifecycle.reset(
            timestamp + timedelta(days=1)
        )

    assert lifecycle.account == before


def test_free_reset(
    make_lifecycle,
    revision,
    timestamp
):

    updated = modify_stage(
        revision,

        "evaluation",

        fees=FeeRules(
            purchase=100,
            reset=0
        )
    )

    lifecycle = make_lifecycle(
        custom_revision=updated
    )

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    lifecycle.reset(
        timestamp + timedelta(days=1)
    )

    assert lifecycle.account.reset_count == 1

    assert lifecycle.account.net_cash_flow_cents == -10_000


def test_active_account_cannot_reset(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    with pytest.raises(LifecycleError):

        lifecycle.reset(timestamp)


def test_funded_account_cannot_reset(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle(
        mode=SimulationMode.FUNDED_ONLY
    )

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    with pytest.raises(LifecycleError):

        lifecycle.reset(
            timestamp + timedelta(days=1)
        )


# ==================================================
# Live transition requirements
# ==================================================

def test_live_transition_all_conditions(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=3)
    )

    rules = LiveTransitionRules(
        minimum_payout_count=1,

        minimum_total_payouts=1000,

        mode=TransitionMode.ALL
    )

    progress = get_live_transition_progress(
        lifecycle.account,
        rules
    )

    assert not progress.eligible


def test_live_transition_any_condition(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=3)
    )

    rules = LiveTransitionRules(
        minimum_payout_count=1,

        minimum_total_payouts=1000,

        mode=TransitionMode.ANY
    )

    progress = get_live_transition_progress(
        lifecycle.account,
        rules
    )

    assert progress.eligible


def test_manual_review_not_automatic(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    rules = LiveTransitionRules(
        minimum_payout_count=1,

        transition_type=TransitionType.MANUAL_REVIEW
    )

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=3)
    )

    progress = get_live_transition_progress(
        lifecycle.account,
        rules
    )

    assert progress.eligible

    assert not progress.automatic_transition_allowed


def test_live_qualification_after_second_payout(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=3)
    )

    assert lifecycle.account.is_active

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=4)
    )

    assert (
        lifecycle.account.status
        is AccountStatus.AWAITING_ACTIVATION
    )

    assert (
        lifecycle.account.pending_stage
        is AccountStage.LIVE
    )


def test_funded_only_mode_does_not_enter_live(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle(
        mode=SimulationMode.FUNDED_ONLY
    )

    funded_payout(lifecycle, timestamp)

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=1)
    )

    assert lifecycle.account.is_active

    assert lifecycle.account.stage is AccountStage.FUNDED

    assert lifecycle.account.pending_stage is None


# ==================================================
# Live activation
# ==================================================

def test_live_starting_balance(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    for day in (3, 4):

        funded_payout(
            lifecycle,
            timestamp + timedelta(days=day)
        )

    lifecycle.activate(
        timestamp + timedelta(days=5)
    )

    account = lifecycle.account

    assert account.stage is AccountStage.LIVE

    assert account.is_active

    assert account.balance_cents == 1_000_000

    assert account.drawdown_floor_cents == 800_000

    assert account.realized_pnl_cents == 0

    assert account.lifetime_payout_count == 2

    assert account.payout_count == 0

    assert account.last_payout_timestamp is None

    assert account.net_cash_flow_cents == 72_500


def test_live_transition_preserves_payouts(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    for day in (3, 4):

        funded_payout(
            lifecycle,
            timestamp + timedelta(days=day)
        )

    lifecycle.activate(
        timestamp + timedelta(days=5)
    )

    account = lifecycle.account

    assert len(account.payout_history) == 2

    assert account.lifetime_gross_withdrawals_cents == 100_000

    assert account.lifetime_trader_payouts_cents == 90_000

    assert len(account.stage_history) == 2

    audit_payout_accounting(account)


def test_live_payout_preserves_lifetime_accounting(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    for day in (3, 4):

        funded_payout(
            lifecycle,
            timestamp + timedelta(days=day)
        )

    t = timestamp + timedelta(days=5)

    lifecycle.activate(t)

    funded_payout(lifecycle, t)

    account = lifecycle.account

    assert account.payout_count == 1

    assert account.lifetime_payout_count == 3

    assert account.net_cash_flow_cents == 117_500

    audit_payout_accounting(account)


# ==================================================
# Cross-stage consistency
# ==================================================

def test_lifetime_consistency_includes_archives(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    activate_funded(lifecycle, timestamp)

    trade_day(
        lifecycle,

        timestamp + timedelta(days=3),

        50_000
    )

    account = lifecycle.account

    stage_rule = ConsistencyRules(
        0.40,

        window=ConsistencyWindow.CURRENT_STAGE
    )

    lifetime_rule = ConsistencyRules(
        0.40,

        window=ConsistencyWindow.ACCOUNT_LIFETIME
    )

    assert not check_payout_consistency(
        account,
        stage_rule
    )

    assert check_payout_consistency(
        account,
        lifetime_rule
    )

    assert len(account.lifetime_session_history) == 4


# ==================================================
# Complete lifecycle reporting
# ==================================================

def run_complete_lifecycle(
    lifecycle,
    timestamp
):

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    lifecycle.reset(
        timestamp + timedelta(days=1)
    )

    pass_evaluation(
        lifecycle,
        timestamp + timedelta(days=1)
    )

    lifecycle.activate(
        timestamp + timedelta(days=4)
    )

    for day in (4, 5):

        funded_payout(
            lifecycle,
            timestamp + timedelta(days=day)
        )

    lifecycle.activate(
        timestamp + timedelta(days=6)
    )

    funded_payout(
        lifecycle,
        timestamp + timedelta(days=6)
    )

    trade_day(
        lifecycle,

        timestamp + timedelta(days=7),

        -250_000
    )

    return lifecycle.result()


def test_complete_lifecycle(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    result = run_complete_lifecycle(
        lifecycle,
        timestamp
    )

    assert result.evaluation_passed

    assert result.reached_funded

    assert result.reached_live

    assert result.evaluation_attempts == 2

    assert result.reset_count == 1

    assert result.payout_count == 3

    assert result.total_trades == 8

    assert result.expenses_cents == 25_000

    assert result.payout_revenue_cents == 135_000

    assert result.net_cash_cents == 110_000

    assert result.profitable

    assert result.received_payout

    assert result.status is AccountStatus.TERMINATED

    assert (
        result.termination_reason
        is TerminationReason.DRAWDOWN
    )


def test_failed_evaluation_result(
    make_lifecycle,
    timestamp
):

    lifecycle = make_lifecycle()

    trade_day(
        lifecycle,
        timestamp,
        -100_000
    )

    result = lifecycle.result()

    assert not result.evaluation_passed

    assert not result.reached_funded

    assert not result.profitable

    assert result.net_cash_cents == -10_000

    assert result.evaluation_attempts == 1


def test_unfinished_evaluation_result(
    make_lifecycle
):

    lifecycle = make_lifecycle()

    result = lifecycle.result()

    assert result.status is AccountStatus.ACTIVE

    assert result.net_cash_cents == -10_000

    assert result.termination_reason is None


def test_strict_fast_lifecycle_equivalence(
    make_lifecycle,
    timestamp
):

    strict = make_lifecycle(strict=True)

    fast = make_lifecycle(strict=False)

    strict_result = run_complete_lifecycle(
        strict,
        timestamp
    )

    fast_result = run_complete_lifecycle(
        fast,
        timestamp
    )

    assert strict_result == fast_result

    assert strict.account == fast.account