import re
import unicodedata
import os
import tempfile

from pathlib import Path
from collections.abc import Callable
from .prop_firm import PropFirm

class DatabaseError(Exception):
    pass

class FirmAlreadyExistsError(DatabaseError):
    pass

class FirmNotFoundError(DatabaseError):
    pass

class InvalidFirmIDError(DatabaseError):
    pass

class DatabaseEntryError(DatabaseError):
    pass

def normalize_identifier(value: str) -> str:

    if not isinstance(value, str):

        raise InvalidFirmIDError(
            "Identifier must be a string"
        )

    value = unicodedata.normalize(
        "NFKD",
        value
    )

    value = value.encode(
        "ascii",
        "ignore"
    ).decode("ascii")

    value = value.lower().strip()

    # Replace groups of special characters
    # with a single underscore.
    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value
    )

    value = value.strip("_")

    if not value:

        raise InvalidFirmIDError(
            "Cannot generate an empty identifier"
        )

    return value

def generate_firm_id(firm: PropFirm) -> str:

    firm_name = normalize_identifier(
        firm.name
    )

    account_name = normalize_identifier(
        firm.ruleset.account_name
    )

    return f"{firm_name}_{account_name}"

def validate_firm_id(firm_id: str) -> None:

    if not isinstance(firm_id, str):

        raise InvalidFirmIDError(
            "Firm ID must be a string"
        )

    if not re.fullmatch(
        r"[a-z0-9]+(?:_[a-z0-9]+)*",
        firm_id
    ):

        raise InvalidFirmIDError(
            f"Invalid firm ID: {firm_id!r}"
        )

class FirmDatabase:

    def __init__(
        self,
        directory: str | Path = "data/firms"
    ):

        self.directory = Path(
            directory
        ).resolve()

        self.directory.mkdir(
            parents=True,
            exist_ok=True
        )

    def __len__(self) -> int:
        return len(self.list_ids())

    def __contains__(
        self,
        firm_id: str
    ) -> bool:
        return self.exists(firm_id)

    def __repr__(self) -> str:
        return (
            f"FirmDatabase("
            f"directory={str(self.directory)!r})"
        )

    def _get_path(
        self,
        firm_id: str
    ) -> Path:

        validate_firm_id(firm_id)
        return self.directory / f"{firm_id}.json"

    def exists(
        self,
        firm_id: str
    ) -> bool:

        path = self._get_path(firm_id)
        return path.exists() or path.is_symlink()

    def add(
        self,
        firm: PropFirm
    ) -> str:

        if not isinstance(firm, PropFirm):
            raise TypeError(
                "Expected a PropFirm instance"
            )

        # Serialize first, which also validates
        # the complete firm configuration.
        content = firm.to_json()
        firm_id = generate_firm_id(firm)
        path = self._get_path(firm_id)

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

    def get(
        self,
        firm_id: str
    ) -> PropFirm:

        path = self._get_path(firm_id)
        if path.is_symlink():
            raise DatabaseEntryError(
                f"Symbolic links are not supported: "
                f"{firm_id}"
            )

        try:
            firm = PropFirm.load(path)

        except FileNotFoundError as exc:
            raise FirmNotFoundError(
                f"Firm not found: {firm_id}"
            ) from exc

        except (ValueError, OSError) as exc:
            raise DatabaseEntryError(
                f"Failed to load {firm_id}: {exc}"
            ) from exc

        try:
            expected_id = generate_firm_id(firm)

        except InvalidFirmIDError as exc:
            raise DatabaseEntryError(
                f"Invalid stored firm name: {firm_id}"
            ) from exc

        if expected_id != firm_id:
            raise DatabaseEntryError(
                f"Identifier mismatch: "
                f"expected {expected_id!r}, "
                f"found {firm_id!r}"
            )

        return firm

    def list_ids(self) -> list[str]:

        identifiers = []
        for path in self.directory.glob("*.json"):

            firm_id = path.stem
            try:
                validate_firm_id(firm_id)

            except InvalidFirmIDError as exc:
                raise DatabaseEntryError(
                    f"Invalid database filename: "
                    f"{path.name}"
                ) from exc
            
            identifiers.append(firm_id)

        return sorted(identifiers)

    def load_all(
        self
    ) -> dict[str, PropFirm]:

        return {
            firm_id: self.get(firm_id)
            for firm_id in self.list_ids()
        }

    def list_all(self) -> list[PropFirm]:

        return list(self.load_all().values())

    def search(
        self,
        query: str
    ) -> dict[str, PropFirm]:

        if not isinstance(query, str):
            raise TypeError(
                "Search query must be a string"
            )

        query = query.casefold().strip()
        results = {}

        for firm_id, firm in self.load_all().items():

            searchable = (
                f"{firm.name} "
                f"{firm.ruleset.account_name} "
                f"{firm_id.replace('_', ' ')}"
            ).casefold()

            if query in searchable:
                results[firm_id] = firm

        return results

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


def atomic_create(
    path: Path,
    content: str
) -> None:

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".firm_",
            suffix=".tmp",
            delete=False
        ) as temporary_file:

            temporary_path = Path(
                temporary_file.name
            )

            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(
                temporary_file.fileno()
            )

        # Publish without overwriting an existing file.
        os.link(
            temporary_path,
            path
        )

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )

    