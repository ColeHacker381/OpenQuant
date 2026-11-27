from dataclasses import dataclass
from pathlib import Path

from .ruleset import Ruleset

from ..core import serialization
from ..core.serialization import (
    SerializationError,
    DeserializationError
)

from .validation import (
    ValidationResult,
    validate_structure
)

@dataclass(frozen=True)
class PropFirm:
    name: str
    ruleset: Ruleset

    def __str__(self) -> str:
        return f"{self.name} - {self.ruleset.account_name}"

    def validate(self) -> ValidationResult:
        result = ValidationResult()
        validate_structure(self, result)

        if not result.is_valid:
            return result
        if not self.name.strip():
            result.add_error(
                "name",
                "Firm name cannot be empty"
            )

        ruleset_result = self.ruleset.validate()

        for error in ruleset_result.errors:
            result.add_error(
                f"ruleset.{error.path}",
                error.message
            )
        for warning in ruleset_result.warnings:
            result.add_warning(
                f"ruleset.{warning.path}",
                warning.message
            )

        return result

    def to_dict(self) -> dict:

        result = self.validate()
        if not result.is_valid:
            raise SerializationError(
                str(result)
            )

        return serialization.to_dict(self)


    def to_json(self) -> str:

        result = self.validate()
        if not result.is_valid:
            raise SerializationError(
                str(result)
            )

        return serialization.to_json(self)


    def save(self, path: str | Path) -> None:

        result = self.validate()
        if not result.is_valid:
            raise SerializationError(
                str(result)
            )

        serialization.save_json(self, path)

    @classmethod
    def _validate_loaded(cls, firm):

        result = firm.validate()
        if not result.is_valid:
            raise DeserializationError(
                "firm",
                str(result)
            )

        return firm


    @classmethod
    def from_dict(cls, data: dict):

        firm = serialization.from_dict(
            cls,
            data
        )
        return cls._validate_loaded(firm)


    @classmethod
    def from_json(cls, content: str):

        firm = serialization.from_json(
            cls,
            content
        )
        return cls._validate_loaded(firm)


    @classmethod
    def load(cls, path: str | Path):

        firm = serialization.load_json(
            cls,
            path
        )
        return cls._validate_loaded(firm)