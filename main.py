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

    from pathlib import Path

    path = Path("data/firms/example_flex_25k.json")

    if not path.exists():

        firm.save(path)

        print(f"Saved firm to {path}")

    loaded_firm = PropFirm.load(path)

    print("\nLoaded firm:")
    print(loaded_firm)

    print("\nLoaded ruleset:")
    print(loaded_firm.ruleset)

    print("\nValidation:")
    print(loaded_firm.validate())

    print("\nRound-trip equality:")
    print(firm == loaded_firm)

    print("\nObject identity:")
    print(firm is loaded_firm)

    print(firm)

    print(
        f"Account Size: "
        f"${firm.ruleset.account_size:,}"
    )

    print(
        f"Evaluation Target: "
        f"${firm.ruleset.evaluation.profit_target:,}"
    )

    print(
        f"Evaluation Cost: "
        f"${firm.ruleset.evaluation.fees.purchase:,}"
    )

    print(
        f"Funded Profit Split: "
        f"{firm.ruleset.funded.payout.profit_split:.0%}"
    )

    print(
        f"Maximum Payout: "
        f"${firm.ruleset.funded.payout.maximum_amount:,}"
    )

    print(
        f"Maximum Accounts: "
        f"{firm.ruleset.max_accounts}"
    )

    test_invalid_configuration()

    print(firm.ruleset.validate())

    print(firm.ruleset.validate_for_simulation())


if __name__ == "__main__":
    main()