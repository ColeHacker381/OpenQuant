from .account import AccountState
from collections.abc import Iterable

from .enums import AccountStage

from .evaluation import (
    EvaluationProgress,
    compile_evaluation,
    get_evaluation_progress,
    process_evaluation
)

from .events import (
    AccountEvent,
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)

from .daily_loss import (
    DailyLossBreach,
    compile_daily_loss,
    process_daily_loss
)

from .accounting import (
    AccountingError,
    assert_accounting_invariants,
    start_session,
    end_session,
    update_equity,
    realize_trade
)

from .drawdown import (
    DrawdownBreach,
    DrawdownConfigurationError,
    DrawdownInvariantError,
    compile_drawdown,
    initialize_drawdown,
    process_drawdown,
    assert_drawdown_invariants
)

from .trading import (
    TradingRequest,
    TradingDecision,
    authorize_trade
)

class EventProcessingError(ValueError):
    pass


class EventOrderError(EventProcessingError):
    pass


class InvalidAccountStateError(EventProcessingError):
    pass


class UnsupportedEventError(EventProcessingError):
    pass

class AccountEngine:

    def __init__(
        self,
        account: AccountState,
        *,
        strict: bool = True
    ):

        if not isinstance(account, AccountState):

            raise TypeError(
                "Expected an AccountState instance"
            )

        if type(strict) is not bool:

            raise TypeError(
                "Strict must be a boolean"
            )

        self.account = account

        self.strict = strict

        self.processed_event_count = 0

        self._handlers = {
            SessionStarted: start_session,

            SessionEnded: end_session,

            EquityUpdated: update_equity,

            TradeRealized: realize_trade
        }

        assert_accounting_invariants(
            self.account
        )

        self.drawdown = None

        self.last_drawdown_breach: DrawdownBreach | None = None

        rules = self.account.stage_rules.drawdown

        if rules is not None:

            self.drawdown = compile_drawdown(
                rules
            )

            if (
                self.account.high_water_mark_cents is None
                and self.account.drawdown_floor_cents is None
            ):

                if self.account.last_event_timestamp is not None:

                    raise DrawdownInvariantError(
                        "Existing account has no drawdown history"
                    )

                initialize_drawdown(
                    self.account,
                    self.drawdown
                )

            assert_drawdown_invariants(
                self.account,
                self.drawdown
            )

        elif (
            self.account.high_water_mark_cents is not None
            or self.account.drawdown_floor_cents is not None
        ):

            raise DrawdownInvariantError(
                "Unexpected drawdown state"
            )

        self.daily_loss = None

        self.last_daily_loss_breach: DailyLossBreach | None = None

        daily_loss_rules = (
            self.account.stage_rules.daily_loss
        )

        if daily_loss_rules is not None:

            self.daily_loss = compile_daily_loss(
                daily_loss_rules
            )

        self.evaluation = None

        if self.account.stage is AccountStage.EVALUATION:

            self.evaluation = compile_evaluation(
                self.account.stage_rules
            )

    def _validate_event(
        self,
        event: AccountEvent
    ) -> None:

        if not isinstance(event, AccountEvent):

            raise TypeError(
                "Expected an AccountEvent instance"
            )

        if type(event) not in self._handlers:

            raise UnsupportedEventError(
                f"Unsupported event: "
                f"{type(event).__name__}"
            )

        if not self.account.is_active:

            raise InvalidAccountStateError(
                f"Account is not active: "
                f"{self.account.status.value}"
            )

        previous = (
            self.account.last_event_timestamp
        )

        if (
            previous is not None
            and event.timestamp < previous
        ):

            raise EventOrderError(
                "Event timestamp precedes "
                "the last processed event"
            )

    def process(
        self,
        event: AccountEvent
    ) -> None:

        self._validate_event(event)

        if self.strict:

            assert_accounting_invariants(
                self.account
            )

            if self.drawdown is not None:

                assert_drawdown_invariants(
                    self.account,
                    self.drawdown
                )

        handler = self._handlers[type(event)]

        try:

            handler(
                self.account,
                event
            )

        except AccountingError as exc:

            raise EventProcessingError(
                str(exc)
            ) from exc

        # Apply the drawdown rules after the
        # financial effects of the event.
        if self.drawdown is not None:

            breach = process_drawdown(
                self.account,
                self.drawdown,
                event
            )

            if breach is not None:

                self.last_drawdown_breach = breach

        # Permanent drawdown takes precedence.
        if (
            self.account.is_active
            and self.daily_loss is not None
        ):

            breach = process_daily_loss(
                self.account,
                self.daily_loss,
                event
            )

            if breach is not None:

                self.last_daily_loss_breach = breach

        if self.strict:

            assert_accounting_invariants(
                self.account
            )

            if self.drawdown is not None:

                assert_drawdown_invariants(
                    self.account,
                    self.drawdown
                )

        self.account.last_event_timestamp = (
            event.timestamp
        )

        self.processed_event_count += 1

        # Evaluation success, failure and expiration.
        if self.evaluation is not None:

            process_evaluation(
                self.account,
                self.evaluation,
                event
            )


    def process_all(
        self,
        events: Iterable[AccountEvent]
    ) -> int:

        processed = 0

        if not self.account.is_active:

            return processed

        for event in events:

            self.process(event)

            processed += 1

            if not self.account.is_active:

                break

        return processed

    def authorize_trade(
        self,
        request: TradingRequest
    ) -> TradingDecision:

        return authorize_trade(
            self.account,
            request
        )

    @property
    def evaluation_progress(
        self
    ) -> EvaluationProgress | None:

        if (
            self.evaluation is None
            or self.account.stage
            is not AccountStage.EVALUATION
        ):

            return None

        return get_evaluation_progress(
            self.account,
            self.evaluation
        )