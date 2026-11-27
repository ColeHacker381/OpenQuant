from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    ConsistencyRules,
    TradingRules,
    PayoutRules,
    LiveTransitionRules,
    EvaluationRules,
    FundedRules,
    LiveRules
)

from openquant.propfirm.database import (
    FirmDatabase,
    FirmAlreadyExistsError,
    generate_firm_id
)

def test_invalid_configuration():

    ruleset = Ruleset(
        account_name="",
        account_size=-25_000,

        evaluation=EvaluationRules(
            profit_target=-1500,

            drawdown=DrawdownRules(
                type=DrawdownType.STATIC,
                amount=-1000
            ),

            fees=FeeRules(
                purchase=-100,
                recurring=50
            ),

            minimum_trading_days=10,
            maximum_trading_days=5
        )
    )

    result = ruleset.validate()

    print(result)
    print(f"\nValid: {result.is_valid}")
    print(f"Errors: {len(result.errors)}")


def main():

    evaluation = EvaluationRules(
        profit_target=1500,

        drawdown=DrawdownRules(
            type=DrawdownType.TRAILING_EOD,
            amount=1000
        ),

        fees=FeeRules(
            purchase=100,
            reset=75
        ),

        minimum_trading_days=3,

        consistency=ConsistencyRules(
            maximum_ratio=0.50
        ),

        trading=TradingRules(
            max_mini_contracts=2,
            max_micro_contracts=20
        )
    )

    funded = FundedRules(
        drawdown=DrawdownRules(
            type=DrawdownType.TRAILING_EOD,
            amount=1000,
            trailing_stop_balance=25_000
        ),

        fees=FeeRules(
            activation=50
        ),

        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=1000,
            minimum_profitable_days=5,
            profitable_day_threshold=100,
            minimum_balance_after_payout=26_000
        ),

        live_transition=LiveTransitionRules(
            minimum_payout_count=5
        )
    )

    live = LiveRules(
        drawdown=DrawdownRules(
            type=DrawdownType.STATIC,
            amount=2000
        ),

        fees=FeeRules(),

        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=100
        ),

        starting_balance=25_000
    )

    ruleset = Ruleset(
        account_name="Flex 25K",
        account_size=25_000,
        evaluation=evaluation,
        funded=funded,
        live=live,
        max_accounts=20
    )

    firm = PropFirm(
        name="Example Trading",
        ruleset=ruleset
    )

    database = FirmDatabase("data/firms")

    firm_id = generate_firm_id(firm)

    try:

        database.add(firm)

        print(f"Added: {firm_id}")

    except FirmAlreadyExistsError:

        print(f"Already exists: {firm_id}")


    print("\nDATABASE CONTENTS")
    print("-" * 50)

    for saved_id, saved_firm in database.load_all().items():

        print(f"ID: {saved_id}")
        print(f"Firm: {saved_firm.name}")

        print(
            f"Account: "
            f"{saved_firm.ruleset.account_name}"
        )

        print(
            f"Size: "
            f"${saved_firm.ruleset.account_size:,}"
        )

        print(
            "Configuration ready: "
            f"{saved_firm.ruleset.validate_for_simulation().is_valid}"
        )

        print("-" * 50)


    print(f"Total accounts: {len(database)}")


    print("\nSEARCH RESULTS")

    for saved_id, saved_firm in database.search(
        "flex"
    ).items():

        print(f"{saved_id}: {saved_firm}")

if __name__ == "__main__":
    main()