from copy import deepcopy
from dataclasses import dataclass, is_dataclass, replace

from ..core.schema import (
    FieldSchema,
    inspect_schema
)

from ..core.serialization import (
    from_dict,
    to_dict,
    serialize
)

from .wizard import ConfigWizard


class EditorError(ValueError):
    pass


@dataclass(frozen=True)
class EditableField:
    path: str
    schema: FieldSchema
    value: object

def discover_fields(
    cls: type,
    data: dict,
    path: str = ""
) -> tuple[EditableField, ...]:

    result = []

    for schema in inspect_schema(cls):

        field_path = (
            f"{path}.{schema.name}"
            if path
            else schema.name
        )

        value = data[schema.name]

        result.append(
            EditableField(
                path=field_path,
                schema=schema,
                value=value
            )
        )

        base_type = schema.base_type

        if (
            isinstance(base_type, type)
            and is_dataclass(base_type)
            and isinstance(value, dict)
        ):

            result.extend(
                discover_fields(
                    base_type,
                    value,
                    field_path
                )
            )

    return tuple(result)


def get_field_value(
    data: dict,
    path: str
):

    current = data

    for part in path.split("."):

        if (
            not isinstance(current, dict)
            or part not in current
        ):

            raise EditorError(
                f"Unknown field: {path}"
            )

        current = current[part]

    return current


def set_field_value(
    data: dict,
    path: str,
    value
) -> None:

    parts = path.split(".")

    current = data

    for part in parts[:-1]:

        if (
            not isinstance(current, dict)
            or part not in current
        ):

            raise EditorError(
                f"Unknown field: {path}"
            )

        current = current[part]

    if (
        not isinstance(current, dict)
        or parts[-1] not in current
    ):

        raise EditorError(
            f"Unknown field: {path}"
        )

    current[parts[-1]] = deepcopy(value)


class ConfigEditor:

    def __init__(
        self,
        obj,
        wizard: ConfigWizard,
        readonly: tuple[str, ...] = ()
    ):

        self.cls = type(obj)

        self.original = to_dict(obj)

        self.data = deepcopy(
            self.original
        )

        self.wizard = wizard

        self.readonly = readonly

    @property
    def is_dirty(self) -> bool:

        return self.data != self.original

    def is_readonly(self, path: str) -> bool:

        return any(
            path == protected
            or path.startswith(protected + ".")
            or protected.startswith(path + ".")
            for protected in self.readonly
        )

    def list_fields(
        self
    ) -> tuple[EditableField, ...]:

        return discover_fields(
            self.cls,
            self.data
        )

    def get_field(
        self,
        path: str
    ) -> EditableField:

        for editable in self.list_fields():

            if editable.path == path:
                return editable

        raise EditorError(
            f"Unknown field: {path}"
        )

    def set_value(
        self,
        path: str,
        value
    ) -> None:

        editable = self.get_field(path)

        if self.is_readonly(path):

            raise EditorError(
                f"Field is read-only: {path}"
            )

        base_type = editable.schema.base_type

        # Normalize nested dataclass configurations
        # to ensure all default fields are populated.
        if (
            isinstance(base_type, type)
            and is_dataclass(base_type)
            and isinstance(value, dict)
        ):

            obj = from_dict(
                base_type,
                value
            )

            value = serialize(obj)

        set_field_value(
            self.data,
            path,
            value
        )

    def reset(self) -> None:

        self.data = deepcopy(
            self.original
        )

    def build(self):

        return from_dict(
            self.cls,
            self.data
        )


    def edit_field(
        self,
        path: str
    ) -> None:

        editable = self.get_field(path)

        if self.is_readonly(path):

            raise EditorError(
                f"Field is read-only: {path}"
            )

        self.wizard.output(
            f"\nEditing: {path}"
        )

        self.wizard.output(
            f"Current value: {editable.value}"
        )

        # For primitive fields and enumerations,
        # Enter should preserve the current value.
        schema = replace(
            editable.schema,
            has_default=True,
            default=editable.value
        )

        value = self.wizard.collect_field(
            schema
        )

        self.set_value(
            path,
            value
        )

    def changes(self) -> dict:

        return find_changes(
            self.original,
            self.data
        )


def find_changes(
    before,
    after,
    path: str = ""
) -> dict:

    if before == after:
        return {}

    if (
        isinstance(before, dict)
        and isinstance(after, dict)
        and before.keys() == after.keys()
    ):

        changes = {}

        for key in before:

            field_path = (
                f"{path}.{key}"
                if path
                else key
            )

            changes.update(
                find_changes(
                    before[key],
                    after[key],
                    field_path
                )
            )

        return changes

    return {
        path: (
            deepcopy(before),
            deepcopy(after)
        )
    }