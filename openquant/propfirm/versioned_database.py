import re

from dataclasses import replace
from datetime import date
from pathlib import Path
from collections.abc import Callable

from .database import (
    FirmDatabase,
    DatabaseError,
    DatabaseEntryError,
    FirmAlreadyExistsError,
    FirmNotFoundError,
    generate_firm_id,
    validate_firm_id,
    atomic_create
)

from .prop_firm import PropFirm

from .revision import (
    FirmRevision,
    RevisionMetadata
)


class RevisionNotFoundError(DatabaseError):
    pass


class RevisionConflictError(DatabaseError):
    pass


class VersionedFirmDatabase:

    def __init__(
        self,
        directory: str | Path = "data/firm_database"
    ):

        self.directory = Path(directory).resolve()

        self.directory.mkdir(
            parents=True,
            exist_ok=True
        )

    def _firm_directory(
        self,
        firm_id: str
    ) -> Path:

        validate_firm_id(firm_id)

        path = self.directory / firm_id

        if path.is_symlink():

            raise DatabaseEntryError(
                f"Symbolic links are not supported: {firm_id}"
            )

        if path.exists() and not path.is_dir():

            raise DatabaseEntryError(
                f"Invalid firm directory: {firm_id}"
            )

        return path

    def _revision_path(
        self,
        firm_id: str,
        revision: int
    ) -> Path:

        if type(revision) is not int or revision < 1:

            raise ValueError(
                "Revision must be a positive integer"
            )

        return (
            self._firm_directory(firm_id)
            / f"revision_{revision:04d}.json"
        )

    def list_revisions(
        self,
        firm_id: str
    ) -> list[int]:

        directory = self._firm_directory(firm_id)

        if not directory.exists():

            raise FirmNotFoundError(
                f"Firm not found: {firm_id}"
            )

        revisions = []

        for path in directory.glob("*.json"):

            match = re.fullmatch(
                r"revision_([0-9]{4,})\.json",
                path.name
            )

            if (
                match is None
                or path.is_symlink()
                or not path.is_file()
            ):

                raise DatabaseEntryError(
                    f"Invalid revision entry: {path.name}"
                )

            revision = int(match.group(1))

            if (
                revision < 1
                or path.name != f"revision_{revision:04d}.json"
            ):

                raise DatabaseEntryError(
                    f"Invalid revision number: {path.name}"
                )

            revisions.append(revision)

        revisions.sort()

        for expected, actual in enumerate(
            revisions,
            start=1
        ):

            if expected != actual:

                raise DatabaseEntryError(
                    f"Missing revision {expected} for {firm_id}"
                )

        return revisions

    def add(
        self,
        firm: PropFirm,
        metadata: RevisionMetadata | None = None
    ) -> str:

        if not isinstance(firm, PropFirm):

            raise TypeError(
                "Expected a PropFirm instance"
            )

        if metadata is None:
            metadata = RevisionMetadata()

        record = FirmRevision(
            firm=firm,
            metadata=replace(
                metadata,
                revision=1
            )
        )

        content = record.to_json()

        firm_id = generate_firm_id(firm)

        directory = self._firm_directory(firm_id)

        directory.mkdir(
            parents=False,
            exist_ok=True
        )

        path = self._revision_path(
            firm_id,
            1
        )

        try:

            atomic_create(
                path,
                content + "\n"
            )

        except FileExistsError as exc:

            raise FirmAlreadyExistsError(
                f"Firm already exists: {firm_id}"
            ) from exc

        return firm_id

    def get_revision(
        self,
        firm_id: str,
        revision: int | None = None
    ) -> FirmRevision:

        revisions = self.list_revisions(firm_id)

        if not revisions:

            raise FirmNotFoundError(
                f"No revisions found: {firm_id}"
            )

        if revision is None:
            revision = revisions[-1]

        path = self._revision_path(
            firm_id,
            revision
        )

        if revision not in revisions:

            raise RevisionNotFoundError(
                f"{firm_id}: revision {revision} not found"
            )

        try:

            record = FirmRevision.from_json(
                path.read_text(encoding="utf-8")
            )

            stored_id = generate_firm_id(
                record.firm
            )

        except (ValueError, OSError, DatabaseError) as exc:

            raise DatabaseEntryError(
                f"Failed to load {path.name}: {exc}"
            ) from exc

        if stored_id != firm_id:

            raise DatabaseEntryError(
                "Stored firm ID does not match its directory"
            )

        if record.metadata.revision != revision:

            raise DatabaseEntryError(
                "Stored revision number does not match filename"
            )

        return record

    def add_revision(
        self,
        firm_id: str,
        firm: PropFirm,
        metadata: RevisionMetadata | None = None,
        *,
        expected_revision: int
    ) -> int:

        current = self.get_revision(firm_id)

        if (
            type(expected_revision) is not int
            or current.metadata.revision != expected_revision
        ):

            raise RevisionConflictError(
                "The firm has changed since it was loaded"
            )

        if generate_firm_id(firm) != firm_id:

            raise DatabaseError(
                "New revision must preserve the firm ID"
            )

        next_revision = expected_revision + 1

        if metadata is None:

            # A changed configuration must not inherit
            # verified status without explicit review.
            firm = replace(
                firm,

                ruleset=replace(
                    firm.ruleset,
                    rules_verified=False
                )
            )

            metadata = RevisionMetadata(
                unresolved_rules=(
                    current.metadata.unresolved_rules
                ),
                notes="Unverified ruleset revision."
            )

        record = FirmRevision(
            firm=firm,
            metadata=replace(
                metadata,
                revision=next_revision
            )
        )

        content = record.to_json()

        path = self._revision_path(
            firm_id,
            next_revision
        )

        try:

            atomic_create(
                path,
                content + "\n"
            )

        except FileExistsError as exc:

            raise RevisionConflictError(
                "Another process created this revision"
            ) from exc

        return next_revision

    def get_effective_revision(
        self,
        firm_id: str,
        effective_on: date
    ) -> FirmRevision:

        if type(effective_on) is not date:

            raise TypeError(
                "Expected a datetime.date"
            )

        candidates = []

        for revision in self.list_revisions(firm_id):

            record = self.get_revision(
                firm_id,
                revision
            )

            raw_date = record.metadata.effective_date

            if raw_date is None:
                continue

            effective_date = date.fromisoformat(
                raw_date
            )

            if effective_date <= effective_on:

                candidates.append(
                    (
                        effective_date,
                        revision,
                        record
                    )
                )

        if not candidates:

            raise RevisionNotFoundError(
                f"No dated ruleset for {firm_id} "
                f"on {effective_on.isoformat()}"
            )

        return max(
            candidates,
            key=lambda item: (item[0], item[1])
        )[2]

    def list_ids(self) -> list[str]:

        identifiers = []

        for directory in self.directory.iterdir():

            if not (
                directory.is_dir()
                or directory.is_symlink()
            ):
                continue

            firm_id = directory.name

            validate_firm_id(firm_id)

            revisions = self.list_revisions(
                firm_id
            )

            if revisions:
                identifiers.append(firm_id)

        return sorted(identifiers)

    def exists(
        self,
        firm_id: str
    ) -> bool:

        try:

            return bool(
                self.list_revisions(firm_id)
            )

        except FirmNotFoundError:

            return False

    def get(
        self,
        firm_id: str
    ) -> PropFirm:

        return self.get_revision(firm_id).firm

    def load_all(self) -> dict[str, PropFirm]:

        return {
            firm_id: self.get(firm_id)
            for firm_id in self.list_ids()
        }

    def list_all(self) -> list[PropFirm]:

        return list(
            self.load_all().values()
        )

    def __len__(self) -> int:

        return len(self.list_ids())

    def __contains__(
        self,
        firm_id: str
    ) -> bool:

        return self.exists(firm_id)

    def search(
        self,
        query: str
    ) -> dict[str, PropFirm]:

        if not isinstance(query, str):

            raise TypeError(
                "Search query must be a string"
            )

        query = query.casefold().strip()

        return {
            firm_id: firm

            for firm_id, firm
            in self.load_all().items()

            if query in (
                f"{firm.name} "
                f"{firm.ruleset.account_name} "
                f"{firm_id.replace('_', ' ')}"
            ).casefold()
        }

    def filter(
        self,
        predicate: Callable[[PropFirm], bool]
    ) -> dict[str, PropFirm]:

        if not callable(predicate):

            raise TypeError(
                "Predicate must be callable"
            )

        return {
            firm_id: firm

            for firm_id, firm
            in self.load_all().items()

            if predicate(firm)
        }

    def history(
        self,
        firm_id: str
    ) -> tuple[FirmRevision, ...]:

        return tuple(
            self.get_revision(
                firm_id,
                revision
            )

            for revision
            in self.list_revisions(firm_id)
        )

    def migrate_from(
        self,
        legacy: FirmDatabase
    ) -> dict[str, str]:

        results = {}

        for firm_id, firm in legacy.load_all().items():

            # Older verification flags do not have
            # supporting source metadata.
            firm = replace(
                firm,

                ruleset=replace(
                    firm.ruleset,
                    rules_verified=False
                )
            )

            if self.exists(firm_id):

                original = self.get_revision(
                    firm_id,
                    revision=1
                )

                if original.firm != firm:

                    raise RevisionConflictError(
                        f"Migration conflict: {firm_id}"
                    )

                results[firm_id] = "already_migrated"

                continue

            self.add(
                firm,

                metadata=RevisionMetadata(
                    notes=(
                        "Migrated from the legacy database. "
                        "Official rule verification required."
                    )
                )
            )

            results[firm_id] = "migrated"

        return results