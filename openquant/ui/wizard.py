import math

from dataclasses import is_dataclass
from enum import Enum
from collections.abc import Callable

from ..core.schema import (
    FieldSchema,
    inspect_schema
)

from ..core.serialization import serialize


class WizardCancelled(Exception):
    pass


class ConfigWizard:

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print
    ):

        self.input = input_fn
        self.output = output_fn

    def _read(self, prompt: str) -> str:

        try:
            response = self.input(prompt).strip()

        except (KeyboardInterrupt, EOFError) as exc:
            raise WizardCancelled(
                "Configuration cancelled"
            ) from exc

        if response.casefold() == ":cancel":
            raise WizardCancelled(
                "Configuration cancelled"
            )

        return response

    def ask_bool(
        self,
        prompt: str,
        default: bool | None = None
    ) -> bool:

        if default is True:
            suffix = "[Y/n]"
        elif default is False:
            suffix = "[y/N]"
        else:
            suffix = "[y/n]"

        while True:

            response = self._read(
                f"{prompt} {suffix}: "
            ).casefold()

            if not response and default is not None:
                return default
            if response in ("y", "yes", "true"):
                return True
            if response in ("n", "no", "false"):
                return False

            self.output(
                "Please enter yes or no."
            )

    def _convert_primitive(
        self,
        response: str,
        expected_type
    ):

        if expected_type is str:
            if not response:
                raise ValueError(
                    "Value cannot be empty"
                )

            return response

        if expected_type is int:
            return int(response)

        if expected_type is float:
            value = float(response)

            if not math.isfinite(value):
                raise ValueError(
                    "Number must be finite"
                )

            return value

        raise TypeError(
            f"Unsupported input type: {expected_type}"
        )

    def ask_primitive(
        self,
        schema: FieldSchema
    ):

        label = schema.label
        if schema.has_default:

            label += f" [{schema.default}]"

        if schema.optional:
            label += " (none = not applicable)"

        while True:

            response = self._read(
                f"{label}: "
            )

            if not response and schema.has_default:
                return serialize(schema.default)

            if (
                schema.optional
                and response.casefold() == "none"
            ):

                return None

            try:

                return self._convert_primitive(
                    response,
                    schema.base_type
                )

            except ValueError as exc:

                self.output(
                    f"Invalid input: {exc}"
                )


    def ask_enum(
        self,
        schema: FieldSchema
    ):

        enum_type = schema.base_type
        members = list(enum_type)
        self.output(
            f"\n{schema.label}:"
        )

        for index, member in enumerate(
            members,
            start=1
        ):

            label = member.name.replace(
                "_", " "
            ).title()

            self.output(
                f"  {index}. {label}"
            )

        if schema.optional:
            self.output(
                "  none. Not applicable"
            )

        prompt = "Selection"
        if schema.has_default:
            prompt += (
                f" [{serialize(schema.default)}]"
            )

        while True:

            response = self._read(
                f"{prompt}: "
            )

            if not response and schema.has_default:
                return serialize(schema.default)

            if (
                schema.optional
                and response.casefold() == "none"
            ):
                return None

            # Accept a numbered selection.
            try:

                index = int(response) - 1
                if 0 <= index < len(members):

                    return serialize(
                        members[index]
                    )

            except ValueError:
                pass

            # Also accept the underlying enum value.
            for member in members:

                if response.casefold() == str(
                    member.value
                ).casefold():

                    return serialize(member)

            self.output(
                "Invalid selection. Please try again."
            )


    def collect_field(
        self,
        schema: FieldSchema
    ):

        if schema.description:
            self.output(
                f"\n{schema.description}"
            )

        base_type = schema.base_type

        # Nested dataclass configurations
        if (
            isinstance(base_type, type)
            and is_dataclass(base_type)
        ):

            if schema.optional:
                enabled = self.ask_bool(
                    f"Configure {schema.label}?"
                )

                if not enabled:
                    return None

            return self.collect(
                base_type,
                title=schema.label
            )

        # Enumerations
        if (
            isinstance(base_type, type)
            and issubclass(base_type, Enum)
        ):

            return self.ask_enum(schema)

        # Boolean values
        if base_type is bool:
            if schema.optional:

                enabled = self.ask_bool(
                    f"Set {schema.label}?"
                )

                if not enabled:
                    return None

            default = (
                schema.default
                if type(schema.default) is bool
                else None
            )

            return self.ask_bool(
                schema.label,
                default
            )

        # Primitive values
        if base_type in (str, int, float):
            return self.ask_primitive(schema)

        raise TypeError(
            f"Unsupported field type: "
            f"{schema.expected_type}"
        )


    def collect(
        self,
        cls: type,
        title: str | None = None
    ) -> dict:

        heading = title or cls.__name__
        self.output(
            f"\n--- {heading.upper()} ---"
        )

        data = {}
        for schema in inspect_schema(cls):

            data[schema.name] = self.collect_field(
                schema
            )

        return data