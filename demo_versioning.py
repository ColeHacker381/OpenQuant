from dataclasses import replace
from tempfile import TemporaryDirectory

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownRules,
    DrawdownType,
    EvaluationRules,
    FeeRules
)

from openquant.propfirm.revision import (
    RevisionMetadata
)

from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase
)


def main():

    with TemporaryDirectory() as directory:

        database = VersionedFirmDatabase(
            directory
        )

        firm = PropFirm(
            name="Example Trading",

            ruleset=Ruleset(
                account_name="Flex 25K",
                account_size=25_000,

                evaluation=EvaluationRules(
                    profit_target=1500,

                    drawdown=DrawdownRules(
                        DrawdownType.STATIC,
                        1000
                    ),

                    fees=FeeRules(
                        purchase=100
                    )
                )
            )
        )

        firm_id = database.add(
            firm,

            metadata=RevisionMetadata(
                effective_date="2026-01-01"
            )
        )

        updated = replace(
            firm,

            ruleset=replace(
                firm.ruleset,

                evaluation=replace(
                    firm.ruleset.evaluation,

                    fees=replace(
                        firm.ruleset.evaluation.fees,
                        purchase=80
                    )
                )
            )
        )

        database.add_revision(
            firm_id,
            updated,

            metadata=RevisionMetadata(
                effective_date="2026-03-01",
                notes="Evaluation price reduced."
            ),

            expected_revision=1
        )

        for record in database.history(firm_id):

            fee = (
                record.firm.ruleset
                .evaluation.fees.purchase
            )

            print(
                f"Revision: {record.metadata.revision}"
            )

            print(
                f"Effective: {record.metadata.effective_date}"
            )

            print(f"Evaluation fee: ${fee:.2f}")

            print(
                f"Fingerprint: {record.fingerprint[:16]}..."
            )

            print()


if __name__ == "__main__":
    main()