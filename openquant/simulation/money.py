from decimal import Decimal, InvalidOperation


def dollars_to_cents(
    value: int | float | str | Decimal
) -> int:

    if isinstance(value, bool):

        raise TypeError(
            "Boolean values are not monetary amounts"
        )

    if not isinstance(
        value,
        (int, float, str, Decimal)
    ):

        raise TypeError(
            "Unsupported monetary value"
        )

    try:

        amount = Decimal(str(value))

    except InvalidOperation as exc:

        raise ValueError(
            "Invalid monetary value"
        ) from exc

    if not amount.is_finite():

        raise ValueError(
            "Monetary value must be finite"
        )

    cents = amount * Decimal(100)

    if cents != cents.to_integral_value():

        raise ValueError(
            "Monetary value must not contain fractional cents"
        )

    return int(cents)


def cents_to_dollars(cents: int) -> Decimal:

    if type(cents) is not int:

        raise TypeError(
            "Cents must be an integer"
        )

    return Decimal(cents) / Decimal(100)


def format_money(cents: int) -> str:

    amount = cents_to_dollars(cents)

    if amount < 0:

        return f"-${abs(amount):,.2f}"

    return f"${amount:,.2f}"