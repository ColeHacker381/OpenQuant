from openquant.propfirm.database import FirmDatabase
from openquant.ui.cli import OpenQuantCLI

def main():

    database = FirmDatabase("data/firms")
    cli = OpenQuantCLI(database)
    cli.run()


if __name__ == "__main__":
    main()