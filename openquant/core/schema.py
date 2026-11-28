from dataclasses import (
    dataclass,
    fields,
    is_dataclass,
    MISSING
)

from types import UnionType

from typing import (
    Any,
    Union,
    get_args,
    get_origin,
    get_type_hints
)

@dataclass(frozen=True)
class FieldSchema:

    name: str
    label: str

    expected_type: Any
    base_type: Any

    optional: bool

    has_default: bool
    default: Any

    description: str = ""

def unwrap_optional(expected_type):

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin in (Union, UnionType):

        non_null_types = [
            item
            for item in args
            if item is not type(None)
        ]

        if (
            type(None) in args
            and len(non_null_types) == 1
        ):

            return non_null_types[0], True

    return expected_type, False

def generate_label(name: str) -> str:

    return name.replace("_", " ").title()

def inspect_schema(cls: type) -> tuple[FieldSchema, ...]:

    if not isinstance(cls, type) or not is_dataclass(cls):
        raise TypeError(
            "Expected a dataclass type"
        )

    hints = get_type_hints(cls)
    schemas = []

    for config_field in fields(cls):
        if not config_field.init:
            continue

        name = config_field.name
        expected_type = hints[name]
        base_type, optional = unwrap_optional(
            expected_type
        )

        has_default = False
        default = None

        if config_field.default is not MISSING:
            has_default = True
            default = config_field.default

        elif config_field.default_factory is not MISSING:
            has_default = True
            default = config_field.default_factory()

        schemas.append(
            FieldSchema(
                name=name,
                label=config_field.metadata.get(
                    "label",
                    generate_label(name)
                ),
                expected_type=expected_type,
                base_type=base_type,
                optional=optional,
                has_default=has_default,
                default=default,
                description=config_field.metadata.get(
                    "description",
                    ""
                )
            )
        )

    return tuple(schemas)