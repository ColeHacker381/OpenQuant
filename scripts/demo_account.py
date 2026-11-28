from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    EvaluationRules,
    FundedRules,
    PayoutRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata
)

from openquant.simulation.account import (
    create_account
)

from openquant.simulation.money import format_money


def main():

    drawdown = DrawdownRules(
        type=DrawdownType.TRAILING_EOD,
        amount=1000
    )

    firm = PropFirm(
        name="Example Trading",

        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,
                drawdown=drawdown,
                fees=FeeRules(purchase=100)
            ),

            funded=FundedRules(
                drawdown=drawdown,
                fees=FeeRules(activation=50),
                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250
                )
            )
        )
    )

    revision = FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            notes="Fictional simulation configuration."
        )
    )

    account_a = create_account(
        "account_001",
        revision,
        require_verified=False
    )

    account_b = create_account(
        "account_002",
        revision,
        require_verified=False
    )

    print(account_a)
    print(account_b)

    print(
        "\nShared rules:",
        account_a.revision is account_b.revision
    )

    # Temporary manual state modification.
    # The engine will handle this in Milestone 2.1.

    account_a.balance_cents += 50_000

    account_a.unrealized_pnl_cents = -12_500

    print("\nUpdated accounts:")

    print(account_a)
    print(account_b)

    print(
        "\nAccount A equity:",
        format_money(account_a.equity_cents)
    )


if __name__ == "__main__":
    main()