from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

import hashlib
import json

from urllib.parse import urlsplit

from ..core import serialization
from .prop_firm import PropFirm
from .validation import ValidationResult


SCHEMA_VERSION = 1


class UnresolvedReason(str, Enum):
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RuleSource:
    title: str
    url: str
    accessed_date: str


@dataclass(frozen=True)
class UnresolvedRule:
    path: str
    reason: UnresolvedReason
    description: str


@dataclass(frozen=True)
class RevisionMetadata:
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    effective_date: str | None = None
    verified_date: str | None = None
    sources: tuple[RuleSource, ...] = ()
    unresolved_rules: tuple[UnresolvedRule, ...] = ()
    notes: str = ""


def parse_iso_date(
    value,
    path: str,
    result: ValidationResult
) -> date | None:

    if value is None:
        return None

    if type(value) is not str:

        result.add_error(
            path,
            "Expected an ISO date string"
        )

        return None

    try:

        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError

        return parsed

    except ValueError:

        result.add_error(
            path,
            "Expected a valid YYYY-MM-DD date"
        )

        return None


def validate_source(
    source: RuleSource,
    path: str,
    result: ValidationResult
) -> None:

    if not isinstance(source, RuleSource):
        result.add_error(
            path,
            "Expected a RuleSource instance"
        )

        return

    if (
        type(source.title) is not str
        or not source.title.strip()
    ):

        result.add_error(
            f"{path}.title",
            "Source title cannot be empty"
        )

    try:
        if type(source.url) is not str:
            raise ValueError

        parsed = urlsplit(source.url)

        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError

    except ValueError:
        result.add_error(
            f"{path}.url",
            "Expected an HTTP or HTTPS URL"
        )

    if source.accessed_date is None:
        result.add_error(
            f"{path}.accessed_date",
            "Access date is required"
        )

    else:
        accessed = parse_iso_date(
            source.accessed_date,
            f"{path}.accessed_date",
            result
        )

        if accessed is not None and accessed > date.today():

            result.add_error(
                f"{path}.accessed_date",
                "Access date cannot be in the future"
            )


def validate_unresolved_rule(
    rule: UnresolvedRule,
    path: str,
    result: ValidationResult
) -> None:

    if not isinstance(rule, UnresolvedRule):
        result.add_error(
            path,
            "Expected an UnresolvedRule instance"
        )

        return

    for name in ("path", "description"):
        value = getattr(rule, name)
        if type(value) is not str or not value.strip():

            result.add_error(
                f"{path}.{name}",
                "Value cannot be empty"
            )

    if not isinstance(
        rule.reason,
        UnresolvedReason
    ):
        result.add_error(
            f"{path}.reason",
            "Invalid unresolved rule reason"
        )


def validate_metadata(
    metadata: RevisionMetadata
) -> ValidationResult:

    result = ValidationResult()
    if not isinstance(metadata, RevisionMetadata):
        result.add_error(
            "metadata",
            "Expected RevisionMetadata"
        )

        return result

    if (
        type(metadata.schema_version) is not int
        or metadata.schema_version != SCHEMA_VERSION
    ):
        result.add_error(
            "schema_version",
            "Unsupported schema version"
        )

    if (
        type(metadata.revision) is not int
        or metadata.revision < 1
    ):
        result.add_error(
            "revision",
            "Revision must be a positive integer"
        )

    parse_iso_date(
        metadata.effective_date,
        "effective_date",
        result
    )

    verified = parse_iso_date(
        metadata.verified_date,
        "verified_date",
        result
    )

    if verified is not None and verified > date.today():
        result.add_error(
            "verified_date",
            "Verification date cannot be in the future"
        )

    if not isinstance(metadata.sources, tuple):
        result.add_error(
            "sources",
            "Sources must be a tuple"
        )

    else:
        for index, source in enumerate(metadata.sources):
            validate_source(
                source,
                f"sources[{index}]",
                result
            )

    if not isinstance(metadata.unresolved_rules, tuple):
        result.add_error(
            "unresolved_rules",
            "Unresolved rules must be a tuple"
        )

    else:
        for index, rule in enumerate(
            metadata.unresolved_rules
        ):
            validate_unresolved_rule(
                rule,
                f"unresolved_rules[{index}]",
                result
            )

    if type(metadata.notes) is not str:
        result.add_error(
            "notes",
            "Expected a string"
        )

    return result


@dataclass(frozen=True)
class FirmRevision:

    firm: PropFirm
    metadata: RevisionMetadata

    def validate(self) -> ValidationResult:

        result = ValidationResult()
        if not isinstance(self.firm, PropFirm):

            result.add_error(
                "firm",
                "Expected a PropFirm instance"
            )

            return result

        firm_result = self.firm.validate()
        metadata_result = validate_metadata(
            self.metadata
        )

        for prefix, sub_result in (
            ("firm", firm_result),
            ("metadata", metadata_result)
        ):

            for error in sub_result.errors:
                result.add_error(
                    f"{prefix}.{error.path}",
                    error.message
                )

            for warning in sub_result.warnings:
                result.add_warning(
                    f"{prefix}.{warning.path}",
                    warning.message
                )

        if not result.is_valid:
            return result

        # A verified revision must have supporting evidence.
        if self.firm.ruleset.rules_verified:
            if not self.metadata.sources:
                result.add_error(
                    "metadata.sources",
                    "Verified rules require supporting sources"
                )

            if self.metadata.verified_date is None:
                result.add_error(
                    "metadata.verified_date",
                    "Verification date is required"
                )

            if self.metadata.effective_date is None:
                result.add_error(
                    "metadata.effective_date",
                    "Effective date is required"
                )

            if self.metadata.unresolved_rules:
                result.add_error(
                    "metadata.unresolved_rules",
                    "Verified rules cannot contain unresolved policies"
                )

        elif self.metadata.verified_date is not None:
            result.add_error(
                "metadata.verified_date",
                "Unverified rules cannot have a verification date"
            )

        return result


    def validate_for_simulation(
        self,
        mode=None,
        *,
        require_verified: bool = True
    ) -> ValidationResult:

        result = self.validate()
        if not result.is_valid:
            return result

        readiness = (
            self.firm.ruleset.validate_for_simulation(
                mode,
                require_verified=require_verified
            )
        )

        result.errors.extend(readiness.errors)
        result.warnings.extend(readiness.warnings)

        if self.metadata.unresolved_rules:
            unsupported = [
                rule
                for rule in self.metadata.unresolved_rules
                if rule.reason is UnresolvedReason.UNSUPPORTED
            ]

            if unsupported:
                result.add_error(
                    "metadata.unresolved_rules",
                    "Unsupported rule behavior prevents simulation"
                )

            elif require_verified:
                result.add_error(
                    "metadata.unresolved_rules",
                    "Unresolved rules prevent verified simulation"
                )

            else:
                result.add_warning(
                    "metadata.unresolved_rules",
                    "Results depend on unresolved rule assumptions"
                )

        return result


    @property
    def rules_fingerprint(self) -> str:

        normalized_firm = replace(
            self.firm,

            ruleset=replace(
                self.firm.ruleset,
                rules_verified=False
            )
        )

        return calculate_fingerprint(
            normalized_firm
        )

    @property
    def fingerprint(self) -> str:

        return calculate_fingerprint(self)

    def to_json(self) -> str:

        result = self.validate()

        if not result.is_valid:

            raise serialization.SerializationError(
                str(result)
            )

        return serialization.to_json(self)

    @classmethod
    def from_json(cls, content: str):

        revision = serialization.from_json(
            cls,
            content
        )

        result = revision.validate()

        if not result.is_valid:

            raise serialization.DeserializationError(
                "revision",
                str(result)
            )

        return revision


def calculate_fingerprint(obj) -> str:

    # Normalize through our typed deserializer.
    # This ensures 1000 and 1000.0 produce the same
    # representation for fields annotated as float.
    data = serialization.to_dict(obj)

    normalized = serialization.from_dict(
        type(obj),
        data
    )

    canonical = json.dumps(
        serialization.to_dict(normalized),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()