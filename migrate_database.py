from openquant.propfirm.database import FirmDatabase

from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase
)


def main():

    legacy = FirmDatabase("data/firms")

    database = VersionedFirmDatabase(
        "data/firm_database"
    )

    results = database.migrate_from(legacy)

    for firm_id, status in results.items():

        print(f"{firm_id}: {status}")


if __name__ == "__main__":
    main()