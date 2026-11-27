import json
import math

from dataclasses import (
    fields,
    is_dataclass,
    MISSING
)

from enum import Enum
from pathlib import Path
from types import UnionType

from typing import (
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints
)

T = TypeVar("T")

class SerializationError(ValueError):
    pass

class DeserializationError(ValueError):

    def __init__(
        self, 
        path: str,
        message: str
    ):
        self.path = path or "root"
        self.message = message

        super().__init__(
            f"{self.path}: {self.message}"
        )

def serialize(obj):

    if isinstance(obj, Enum):
        return serialize(obj.value)

    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            config_field.name: serialize(
                getattr(obj, config_field.name)
            ) for config_field in fields(obj)
        }

    if obj is None:
        return None

    if type(obj) in (str, int, bool):
        return obj

    if type(obj) is float:
        if not math.isfinite(obj):
            raise SerializationError(
                "Cannot serialize nonfinite numbers"
            )

        return obj

    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]

    if isinstance(obj, dict):
        if any(type(key) is not str for key in obj):
            raise SerializationError(
                "Dictionary keys must be strings"
            )

        return {
            key: serialize(value)
            for key, value in obj.items()
        }

    raise SerializationError(
        f"Unsupported type: {type(obj).__name__}"
    )


def deserialize(
    value,
    expected_type: type[T],
    path: str = ""
) -> T:
    
    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # Optional and union types
    if origin in (Union, UnionType):

        non_null_types = [
            option
            for option in args
            if option is not type(None)
        ]

        # Preserve detailed errors for Optional[T]
        if (
            type(None) in args
            and len(non_null_types) == 1
        ):

            if value is None:
                return None

            return deserialize(
                value,
                non_null_types[0],
                path
            )

        # General union
        for option in args:
            try:
                return deserialize(
                    value,
                    option,
                    path
                )

            except DeserializationError:
                continue

        raise DeserializationError(
            path,
            f"Value does not match {expected_type}"
        )

    # Lists
    if origin is list:

        if not isinstance(value, list):

            raise DeserializationError(
                path,
                "Expected an array"
            )

        item_type = args[0]

        return [
            deserialize(
                item,
                item_type,
                f"{path}[{index}]"
            )
            for index, item in enumerate(value)
        ]

    # Tuples
    if origin is tuple:

        if not isinstance(value, list):

            raise DeserializationError(
                path,
                "Expected an array"
            )

        # Variable-length tuple[T, ...]
        if len(args) == 2 and args[1] is Ellipsis:

            return tuple(
                deserialize(
                    item,
                    args[0],
                    f"{path}[{index}]"
                )
                for index, item in enumerate(value)
            )

        # Fixed-length tuple[T1, T2, ...]
        if len(value) != len(args):

            raise DeserializationError(
                path,
                f"Expected {len(args)} items"
            )

        return tuple(
            deserialize(
                item,
                item_type,
                f"{path}[{index}]"
            )
            for index, (item, item_type)
            in enumerate(zip(value, args))
        )

    # Dictionaries
    if origin is dict:

        if not isinstance(value, dict):

            raise DeserializationError(
                path,
                "Expected an object"
            )

        key_type, item_type = args

        if key_type is not str:

            raise DeserializationError(
                path,
                "Only string dictionary keys are supported"
            )

        result = {}

        for key, item in value.items():

            if type(key) is not str:

                raise DeserializationError(
                    path,
                    "Expected string dictionary keys"
                )

            item_path = (
                f"{path}.{key}"
                if path
                else key
            )

            result[key] = deserialize(
                item,
                item_type,
                item_path
            )

        return result

    if expected_type is type(None):
        if value is None:
            return None

        return DeserializationError(
            path,
            "Expected null"
        )

    if expected_type is bool:
        if type(value) is bool:
            return value

        raise DeserializationError(
            path,
            "Expected a boolean"
        )

    if expected_type is int:
        if type(value) is int:
            return value

        raise DeserializationError(
            path,
            "Expected an integer"
        )

    if expected_type is float:
        if type(value) not in (int, float):
            raise DeserializationError(
                path,
                "Expected a number"
            )

        try:
            number = float(value)
        except OverflowError as exc:
            raise DeserializationError(
                path,
                "Number exceeds supported range"
            ) from exc

        if not math.isfinite(number):
            raise DeserializationError(
                path,
                "Expected a finite number"
            )

        return number

    if expected_type is str:
        if type(value) is str:
            return value

        raise DeserializationError(
            path,
            "Expected a string"
        )

    # Enumerations
    if (
        isinstance(expected_type, type)
        and issubclass(expected_type, Enum)
    ):

        for member in expected_type:

            if (
                type(value) is type(member.value)
                and value == member.value
            ):
                return member

        valid_values = [
            member.value
            for member in expected_type
        ]

        raise DeserializationError(
            path,
            (
                f"Invalid value {value!r}. "
                f"Expected one of {valid_values}"
            )
        )

    # Nested dataclasses
    if (
        isinstance(expected_type, type)
        and is_dataclass(expected_type)
    ):

        return deserialize_dataclass(
            value,
            expected_type,
            path
        )

    raise DeserializationError(
        path,
        f"Unsupported type: {expected_type}"
    )


def deserialize_dataclass(
    data: dict,
    cls: type[T],
    path: str = ""
) -> T:

    if not isinstance(data, dict):
        raise DeserializationError(
            path,
            "Expected an object"
        )

    config_fields = {
        config_field.name: config_field
        for config_field in fields(cls)
        if config_field.init
    }

    hints = get_type_hints(cls)

    unknown_fields = (
        set(data.keys()) - set(config_fields.keys())
    )

    if unknown_fields:
        names = ", ".join(
            sorted(map(str, unknown_fields))
        )

        raise DeserializationError(
            path,
            f"Unknown fields: {names}"
        )

    kwargs = {}
    for name, config_field in config_fields.items():

        field_path = f"{path}.{name}" if path else name
        if name not in data:

            has_default = config_field.default is not MISSING
            has_factory = config_field.default_factory is not MISSING

            if has_default or has_factory:
                continue

            raise DeserializationError(
                field_path,
                "Required field is missing"
            )

        kwargs[name] = deserialize(
            data[name],
            hints[name],
            field_path
        )

    return cls(**kwargs)


def to_dict(obj) -> dict:
    data = serialize(obj)
    if not isinstance(data, dict):
        raise SerializationError(
            "Root object must serialize to a dictionary"
        )

    return data

def from_dict(
    cls: type[T],
    data: dict
) -> T:
    if not isinstance(data, dict):
        raise DeserializationError(
            "root",
            "Expected a dictionary"
        )

    return deserialize(data, cls)

def to_json(
    obj,
    indent: int = 4
) -> str:

    return json.dumps(
        to_dict(obj),
        indent=indent,
        allow_nan=False
    )

def reject_duplicate_keys(pairs):

    result = {}
    for key, value in pairs:
        if key in result:
            raise DeserializationError(
                "json",
                f"Duplicate key: {key}"
            )

        result[key] = value
    return result


def reject_invalid_constant(value):

    raise DeserializationError(
        "json",
        f"Invalid numeric constant: {value}"
    )

def from_json(
    cls: type[T],
    content: str
) -> T:

    try:
        data = json.loads(
            content,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_invalid_constant
        )

    except json.JSONDecodeError as exc:
        raise DeserializationError(
            "json",
            (
                f"Invalid JSON at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}"
            )
        ) from exc

    return from_dict(cls, data)

def save_json(
    obj,
    path: str | Path
) -> None:

    path = Path(path)
    content = to_json(obj)

    with path.open(
        "x",
        encoding="utf-8"
    ) as file:

        file.write(content + "\n")


def load_json(
    cls: type[T],
    path: str | Path
) -> T:

    path = Path(path)
    content = path.read_text(
        encoding="utf-8"
    )

    return from_json(cls, content)