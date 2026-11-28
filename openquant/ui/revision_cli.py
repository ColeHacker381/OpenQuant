from dataclasses import replace

from ..core.serialization import (
    DeserializationError,
    SerializationError
)

from ..core.serialization import from_dict

from ..propfirm.revision import (
    RevisionMetadata,
    RuleSource,
    UnresolvedRule,
    UnresolvedReason,
    FirmRevision
)

from ..propfirm.versioned_database import (
    VersionedFirmDatabase
)

from .editor import (
    ConfigEditor,
    EditorError
)

from .wizard import ConfigWizard


class RevisionCLI:

    def __init__(
        self,
        database: VersionedFirmDatabase,
        wizard: ConfigWizard
    ):

        self.database = database
        self.wizard = wizard

        self.output = wizard.output

    def show_history(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        history = self.database.history(
            firm_id
        )

        self.output(
            f"\nREVISION HISTORY: {firm_id}"
        )

        for record in history:

            metadata = record.metadata

            self.output(
                f"\nRevision: {metadata.revision}"
            )

            self.output(
                f"Effective: "
                f"{metadata.effective_date or 'Unknown'}"
            )

            self.output(
                f"Verified: "
                f"{record.firm.ruleset.rules_verified}"
            )

            self.output(
                f"Unresolved rules: "
                f"{len(metadata.unresolved_rules)}"
            )

            self.output(
                f"Fingerprint: "
                f"{record.fingerprint[:16]}..."
            )

            self.output(
                f"Notes: {metadata.notes}"
            )

    def inspect_revision(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        raw = self.wizard._read(
            "Revision number (Enter for latest): "
        )

        if raw:

            try:
                revision = int(raw)

                if revision < 1:
                    raise ValueError

            except ValueError:

                self.output(
                    "Revision must be a positive integer."
                )

                return

        else:
            revision = None

        record = self.database.get_revision(
            firm_id,
            revision
        )

        self.output(
            record.to_json()
        )

    def edit_revision(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        current = self.database.get_revision(
            firm_id
        )

        editor = ConfigEditor(
            current.firm,

            wizard=self.wizard,

            readonly=(
                "name",
                "ruleset.account_name",
                "ruleset.account_size",
                "ruleset.rules_verified"
            )
        )

        while True:

            self.output(
                "\nRULESET EDITOR"
            )

            self.output("1. List fields")
            self.output("2. Edit field")
            self.output("3. Show changes")
            self.output("4. Validate")
            self.output("5. Reset changes")
            self.output("6. Save revision")
            self.output("0. Cancel")

            selection = self.wizard._read(
                "Selection: "
            )

            if selection == "0":
                return None

            try:

                if selection == "1":

                    for editable in editor.list_fields():

                        if editor.is_readonly(
                            editable.path
                        ):
                            continue

                        value = editable.value

                        if isinstance(value, dict):
                            value = "<configuration>"

                        self.output(
                            f"{editable.path}: {value}"
                        )

                elif selection == "2":

                    path = self.wizard._read(
                        "Field path: "
                    )

                    editor.edit_field(path)

                elif selection == "3":

                    self.show_changes(editor)

                elif selection == "4":

                    firm = editor.build()

                    self.output(
                        str(firm.validate())
                    )

                elif selection == "5":

                    if self.wizard.ask_bool(
                        "Discard all changes?",
                        default=False
                    ):

                        editor.reset()

                elif selection == "6":

                    if not editor.is_dirty:

                        self.output(
                            "No changes to save."
                        )

                        continue

                    firm = editor.build()

                    result = firm.validate()

                    if not result.is_valid:

                        self.output(str(result))

                        continue

                    self.show_changes(editor)

                    if not self.wizard.ask_bool(
                        "Save new revision?",
                        default=False
                    ):
                        continue

                    revision = (
                        self.database.add_revision(
                            firm_id,
                            firm,

                            expected_revision=(
                                current.metadata.revision
                            )
                        )
                    )

                    self.output(
                        f"Saved revision {revision}"
                    )

                    return revision

                else:

                    self.output(
                        "Invalid selection."
                    )

            except (
                EditorError,
                DeserializationError,
                SerializationError
            ) as exc:

                self.output(
                    f"Configuration error: {exc}"
                )

    def show_changes(
        self,
        editor: ConfigEditor
    ):

        changes = editor.changes()

        if not changes:

            self.output(
                "No unsaved changes."
            )

            return

        for path, (before, after) in changes.items():

            self.output(f"\n{path}")

            self.output(
                f"  Previous: {before}"
            )

            self.output(
                f"  Updated:  {after}"
            )

    def collect_sources(
        self,
        existing: tuple[RuleSource, ...] = ()
    ) -> tuple[RuleSource, ...]:

        sources = list(existing)

        if sources:

            self.output(
                "\nEXISTING SOURCES"
            )

            for source in sources:

                self.output(
                    f"{source.title}: {source.url}"
                )

            if self.wizard.ask_bool(
                "Replace existing source list?",
                default=False
            ):

                sources.clear()

        while self.wizard.ask_bool(
            "Add documentation source?",
            default=False
        ):

            data = self.wizard.collect(
                RuleSource
            )

            sources.append(
                RuleSource(**data)
            )

        return tuple(sources)


    def collect_unresolved_rules(
        self,
        existing: tuple[UnresolvedRule, ...] = ()
    ) -> tuple[UnresolvedRule, ...]:

        unresolved = list(existing)

        if unresolved:

            self.output(
                "\nEXISTING UNRESOLVED RULES"
            )

            for rule in unresolved:

                self.output(
                    f"{rule.path}: "
                    f"{rule.reason.value} — "
                    f"{rule.description}"
                )

            if self.wizard.ask_bool(
                "Replace unresolved rule list?",
                default=False
            ):

                self.output(
                    "Reenter every remaining unresolved rule."
                )

                unresolved.clear()

        while self.wizard.ask_bool(
            "Add unresolved rule?",
            default=False
        ):

            data = self.wizard.collect(
                UnresolvedRule
            )

            rule = from_dict(
                UnresolvedRule,
                data
            )

            unresolved.append(rule)

        return tuple(unresolved)


    def ask_optional_date(
        self,
        label: str,
        current: str | None = None
    ) -> str | None:

        response = self.wizard._read(
            f"{label} [{current or 'None'}]: "
        )

        if not response:
            return current

        if response.casefold() == "none":
            return None

        return response


    def ask_optional_date(
        self,
        label: str,
        current: str | None = None
    ) -> str | None:

        response = self.wizard._read(
            f"{label} [{current or 'None'}]: "
        )

        if not response:
            return current

        if response.casefold() == "none":
            return None

        return response


    def verify_revision(self):

        firm_id = self.wizard._read(
            "Firm ID: "
        )

        current = self.database.get_revision(
            firm_id
        )

        metadata = current.metadata

        sources = self.collect_sources(
            metadata.sources
        )

        unresolved = self.collect_unresolved_rules(
            metadata.unresolved_rules
        )

        effective_date = self.ask_optional_date(
            "Effective date (YYYY-MM-DD)",
            metadata.effective_date
        )

        verified = False
        verified_date = None

        if unresolved:

            self.output(
                "Unresolved rules remain. "
                "Verification is unavailable."
            )

        else:

            self.output(
                "\nVerification confirms that all "
                "applicable rules have been checked, "
                "including restrictions not represented "
                "by default values."
            )

            verified = self.wizard.ask_bool(
                "Confirm complete rule verification?",
                default=False
            )

            if verified:

                verified_date = self.ask_optional_date(
                    "Verification date (YYYY-MM-DD)",
                    metadata.verified_date
                )

        notes = self.wizard._read(
            "Research notes (Enter to retain): "
        )

        firm = replace(
            current.firm,

            ruleset=replace(
                current.firm.ruleset,
                rules_verified=verified
            )
        )

        updated_metadata = RevisionMetadata(
            schema_version=metadata.schema_version,

            revision=metadata.revision + 1,

            effective_date=effective_date,

            verified_date=verified_date,

            sources=sources,

            unresolved_rules=unresolved,

            notes=notes or metadata.notes
        )

        record = FirmRevision(
            firm=firm,
            metadata=updated_metadata
        )

        result = record.validate()

        self.output(
            str(result)
        )

        if not result.is_valid:

            self.output(
                "Research revision not saved."
            )

            return None

        self.output(
            record.to_json()
        )

        if not self.wizard.ask_bool(
            "Save research revision?",
            default=False
        ):

            return None

        revision = self.database.add_revision(
            firm_id,
            firm,
            updated_metadata,

            expected_revision=(
                metadata.revision
            )
        )

        self.output(
            f"Saved research revision {revision}"
        )

        return revision

