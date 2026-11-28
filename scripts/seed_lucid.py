from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    TradingRules,
    ConsistencyRules,
    EvaluationRules
)

from openquant.propfirm.revision import (
    RevisionMetadata,
    RuleSource,
    UnresolvedRule,
    UnresolvedReason
)

from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase
)


def main():

    database = VersionedFirmDatabase(
        "data/firm_database"
    )

    firm = PropFirm(
        name="Lucid Trading",

        ruleset=Ruleset(
            account_name="LucidFlex 25K Standard",

            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1250,

                drawdown=DrawdownRules(
                    type=DrawdownType.TRAILING_EOD,
                    amount=1000
                ),

                # Purchase price remains unresolved.
                fees=FeeRules(),

                minimum_trading_days=3,

                consistency=ConsistencyRules(
                    maximum_ratio=0.40
                ),

                trading=TradingRules(
                    max_mini_contracts=2,
                    max_micro_contracts=20
                )
            ),

            funded=None,

            live=None,

            rules_verified=False
        )
    )

    metadata = RevisionMetadata(
        sources=(
            RuleSource(
                title="LucidFlex Evaluation Account",

                url=(
                    "https://support.lucidtrading.com"
                    "/en/articles/12945790"
                    "-lucidflex-evaluation-account"
                ),

                accessed_date="2026-11-27"
            ),

            RuleSource(
                title="LucidFlex Funded Account",

                url=(
                    "https://support.lucidtrading.com"
                    "/en/articles/12945795"
                    "-lucidflex-funded-account"
                ),

                accessed_date="2026-11-27"
            ),

            RuleSource(
                title="LucidFlex Consistency Percentage",

                url=(
                    "https://support.lucidtrading.com"
                    "/en/articles/12945805"
                    "-lucidflex-consistency-percentage"
                ),

                accessed_date="2026-11-27"
            )
        ),

        unresolved_rules=(
            UnresolvedRule(
                path="evaluation.fees.purchase",
                reason=UnresolvedReason.UNKNOWN,
                description="Confirm checkout price."
            ),

            UnresolvedRule(
                path="evaluation.drawdown",
                reason=UnresolvedReason.UNKNOWN,
                description=(
                    "Confirm exact trailing cap and "
                    "breach semantics."
                )
            ),

            UnresolvedRule(
                path="evaluation.trading",
                reason=UnresolvedReason.UNKNOWN,
                description=(
                    "Confirm session restrictions, "
                    "news restrictions and inactivity rules."
                )
            ),

            UnresolvedRule(
                path="funded",
                reason=UnresolvedReason.UNKNOWN,
                description=(
                    "Complete funded payout and "
                    "eligibility requirements."
                )
            ),

            UnresolvedRule(
                path="funded.trading.scaling",
                reason=UnresolvedReason.UNSUPPORTED,
                description=(
                    "Funded contract scaling requires "
                    "additional ruleset functionality."
                )
            ),

            UnresolvedRule(
                path="live",
                reason=UnresolvedReason.UNKNOWN,
                description=(
                    "Confirm live account policies "
                    "and transition requirements."
                )
            ),

            UnresolvedRule(
                path="max_accounts",
                reason=UnresolvedReason.UNKNOWN,
                description=(
                    "Confirm account ownership and "
                    "activation limits."
                )
            )
        ),

        notes=(
            "Initial research draft. Standard 40% "
            "evaluation consistency. Checkout options "
            "must be confirmed. Placeholder defaults "
            "are not verified firm policies."
        )
    )

    firm_id = database.add(
        firm,
        metadata
    )

    print(f"Created: {firm_id}")


if __name__ == "__main__":

    main()