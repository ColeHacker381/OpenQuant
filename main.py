from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase
)

from openquant.ui.cli import OpenQuantCLI


def main():

    database = VersionedFirmDatabase(
        "data/firm_database"
    )

    cli = OpenQuantCLI(database)

    cli.run()


if __name__ == "__main__":
    main()