from .account import AccountState
from collections.abc import Iterable

from .events import (
    AccountEvent,
    SessionStarted,
    SessionEnded,
    EquityUpdated,
    TradeRealized
)

from .accounting import (
    AccountingError,
    assert_accounting_invariants,
    start_session,
    end_session,
    update_equity,
    realize_trade
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

        if self.strict:

            assert_accounting_invariants(
                self.account
            )

        self.account.last_event_timestamp = (
            event.timestamp
        )

        self.processed_event_count += 1

    def process_all(
        self,
        events: Iterable[AccountEvent]
    ) -> int:

        processed = 0

        for event in events:

            self.process(event)

            processed += 1

        return processed

