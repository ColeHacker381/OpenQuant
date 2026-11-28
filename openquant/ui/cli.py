from collections.abc import Callable

from ..core.serialization import (
    SerializationError,
    DeserializationError
)

from ..propfirm.versioned_database import (
    VersionedFirmDatabase
)

from ..propfirm.prop_firm import PropFirm

from ..propfirm.database import (
    FirmDatabase,
    DatabaseError,
    FirmAlreadyExistsError,
    generate_firm_id
)

from .wizard import (
    ConfigWizard,
    WizardCancelled
)


class OpenQuantCLI:

    def __init__(
        self,
        database: FirmDatabase,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print
    ):

        self.database = database
        self.input = input_fn
        self.output = output_fn
        self.wizard = ConfigWizard(
            input_fn=input_fn,
            output_fn=output_fn
        )


    def add_firm(self):

        self.output(
            "\nADD PROP FIRM"
        )

        self.output(
            "Enter :cancel at any prompt to cancel."
        )

        self.output(
            "Defaults are modeling assumptions, not "
            "verified firm policies."
        )

        try:

            data = self.wizard.collect(
                PropFirm,
                title="Prop Firm Configuration"
            )

            if data["ruleset"]["rules_verified"]:

                self.output(
                    "New firms are saved as unverified drafts. "
                    "Source documentation must be added before "
                    "verification."
                )

            data["ruleset"]["rules_verified"] = False

            firm = PropFirm.from_dict(data)
            firm_id = generate_firm_id(firm)

            self.output(
                f"\nGenerated ID: {firm_id}"
            )

            self.output(
                firm.to_json()
            )

            result = firm.validate()

            self.output(
                str(result)
            )

            readiness = (
                firm.ruleset.validate_for_simulation(
                    require_verified=True
                )
            )

            self.output(
                "\nVerified lifecycle readiness:"
            )

            self.output(
                str(readiness)
            )

            confirmed = self.wizard.ask_bool(
                "Save this configuration?",
                default=False
            )

            if not confirmed:

                self.output(
                    "Configuration not saved."
                )

                return None

            firm_id = self.database.add(firm)

            self.output(
                f"Saved: {firm_id}"
            )

            return firm_id

        except WizardCancelled:
            self.output(
                "Configuration cancelled."
            )

        except FirmAlreadyExistsError as exc:
            self.output(
                f"Duplicate account: {exc}"
            )

        except (
            DeserializationError,
            SerializationError,
            DatabaseError,
            OSError
        ) as exc:

            self.output(
                f"Unable to save firm: {exc}"
            )

        return None


    def display_firms(
        self,
        firms: dict[str, PropFirm]
    ):

        if not firms:
            self.output(
                "No firms found."
            )

            return

        self.output(
            f"\n{len(firms)} account offering(s)"
        )

        for firm_id, firm in firms.items():
            verification = (
                "Verified"
                if firm.ruleset.rules_verified
                else "Unverified"
            )

            self.output(
                f"\nID: {firm_id}"
            )

            self.output(
                f"Firm: {firm.name}"
            )

            self.output(
                f"Account: "
                f"{firm.ruleset.account_name}"
            )

            self.output(
                f"Size: "
                f"${firm.ruleset.account_size:,}"
            )

            self.output(
                f"Rules: {verification}"
            )


    def list_firms(self):

        self.display_firms(
            self.database.load_all()
        )


    def search_firms(self):

        query = self.wizard._read(
            "Search firms: "
        )

        self.display_firms(
            self.database.search(query)
        )


    def inspect_firm(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        firm = self.database.get(firm_id)
        self.output(
            firm.to_json()
        )


    def validate_firm(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        firm = self.database.get(firm_id)

        self.output(
            "\nCONFIGURATION VALIDATION"
        )

        self.output(
            str(firm.validate())
        )

        self.output(
            "\nFULL LIFECYCLE READINESS"
        )

        self.output(
            str(
                firm.ruleset.validate_for_simulation()
            )
        )

        self.output(
            "\nVERIFIED LIFECYCLE READINESS"
        )

        self.output(
            str(
                firm.ruleset.validate_for_simulation(
                    require_verified=True
                )
            )
        )


    def display_menu(self):

        self.output(
            "\n=============================="
        )

        self.output(
            "        OPENQUANT v0.1.0"
        )

        self.output(
            "=============================="
        )

        self.output("1. Add prop firm")
        self.output("2. List prop firms")
        self.output("3. Search prop firms")
        self.output("4. Inspect prop firm")
        self.output("5. Validate prop firm")
        self.output("0. Exit")


    def run(self):

        actions = {
            "1": self.add_firm,
            "2": self.list_firms,
            "3": self.search_firms,
            "4": self.inspect_firm,
            "5": self.validate_firm
        }

        while True:

            self.display_menu()
            try:

                selection = self.wizard._read(
                    "\nSelection: "
                )

            except WizardCancelled:
                self.output(
                    "Exiting OpenQuant."
                )

                return

            if selection == "0":
                self.output(
                    "Exiting OpenQuant."
                )

                return

            action = actions.get(selection)

            if action is None:
                self.output(
                    "Invalid selection."
                )

                continue

            try:
                action()

            except WizardCancelled:
                self.output(
                    "Operation cancelled."
                )

            except (DatabaseError, OSError) as exc:

                self.output(
                    f"Database error: {exc}"
                )