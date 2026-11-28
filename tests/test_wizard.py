import pytest

from dataclasses import (
    dataclass,
    field,
    replace
)

from enum import Enum

from openquant.core.schema import (
    inspect_schema,
    unwrap_optional,
    generate_label
)

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    EvaluationRules
)

from openquant.propfirm.database import (
    FirmDatabase
)

from openquant.ui.wizard import (
    ConfigWizard,
    WizardCancelled
)

from openquant.ui.cli import OpenQuantCLI


# ==================================================
# Generic test configurations
# ==================================================

class ExampleMode(str, Enum):

    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True)
class ChildConfig:

    amount: float


@dataclass(frozen=True)
class ExampleConfig:

    name: str

    count: int

    ratio: float

    enabled: bool

    mode: ExampleMode

    optional_value: float | None = None

    child: ChildConfig | None = None


# ==================================================
# Test helpers
# ==================================================

def create_wizard(responses):

    responses = iter(responses)

    output = []

    wizard = ConfigWizard(
        input_fn=lambda prompt: next(responses),
        output_fn=output.append
    )

    return wizard, output


def create_cli(database, responses=()):

    responses = iter(responses)

    output = []

    cli = OpenQuantCLI(
        database=database,
        input_fn=lambda prompt: next(responses),
        output_fn=output.append
    )

    return cli, output


@pytest.fixture
def database(tmp_path):

    return FirmDatabase(
        tmp_path / "firms"
    )


@pytest.fixture
def firm():

    return PropFirm(
        name="Example Trading",
        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000
        )
    )


# ==================================================
# Schema inspection
# ==================================================

def test_unwrap_primitive():

    assert unwrap_optional(float) == (
        float,
        False
    )


def test_unwrap_optional():

    assert unwrap_optional(float | None) == (
        float,
        True
    )


def test_unwrap_nested_dataclass():

    assert unwrap_optional(
        ChildConfig | None
    ) == (ChildConfig, True)


def test_generate_label():

    assert generate_label(
        "minimum_trading_days"
    ) == "Minimum Trading Days"


def test_inspect_schema():

    schemas = inspect_schema(ExampleConfig)

    names = [
        schema.name
        for schema in schemas
    ]

    assert names == [
        "name",
        "count",
        "ratio",
        "enabled",
        "mode",
        "optional_value",
        "child"
    ]


def test_required_field_schema():

    schemas = {
        schema.name: schema
        for schema in inspect_schema(ExampleConfig)
    }

    schema = schemas["count"]

    assert schema.base_type is int
    assert schema.optional is False
    assert schema.has_default is False


def test_optional_field_schema():

    schemas = {
        schema.name: schema
        for schema in inspect_schema(ExampleConfig)
    }

    schema = schemas["optional_value"]

    assert schema.base_type is float
    assert schema.optional is True
    assert schema.has_default is True
    assert schema.default is None


def test_enum_schema():

    schemas = {
        schema.name: schema
        for schema in inspect_schema(ExampleConfig)
    }

    assert (
        schemas["mode"].base_type
        is ExampleMode
    )


def test_nested_schema():

    schemas = {
        schema.name: schema
        for schema in inspect_schema(ExampleConfig)
    }

    assert (
        schemas["child"].base_type
        is ChildConfig
    )


def test_schema_metadata():

    @dataclass(frozen=True)
    class Config:

        amount: float = field(
            metadata={
                "label": "Amount ($)",
                "description": "Dollar amount"
            }
        )

    schema = inspect_schema(Config)[0]

    assert schema.label == "Amount ($)"
    assert schema.description == "Dollar amount"


def test_schema_default_factory():

    @dataclass
    class Config:

        values: list = field(
            default_factory=list
        )

    first = inspect_schema(Config)[0]
    second = inspect_schema(Config)[0]

    assert first.has_default
    assert first.default == []
    assert first.default is not second.default


def test_schema_ignores_non_init_fields():

    @dataclass
    class Config:

        value: int

        hidden: int = field(
            default=0,
            init=False
        )

    schemas = inspect_schema(Config)

    assert len(schemas) == 1
    assert schemas[0].name == "value"


def test_invalid_schema():

    with pytest.raises(TypeError):

        inspect_schema(dict)


# ==================================================
# Boolean prompts
# ==================================================

@pytest.mark.parametrize(
    "response",
    ["y", "yes", "true", "YES"]
)
def test_boolean_true(response):

    wizard, _ = create_wizard([response])

    assert wizard.ask_bool("Enable?") is True


@pytest.mark.parametrize(
    "response",
    ["n", "no", "false", "NO"]
)
def test_boolean_false(response):

    wizard, _ = create_wizard([response])

    assert wizard.ask_bool("Enable?") is False


def test_boolean_default_true():

    wizard, _ = create_wizard([""])

    assert wizard.ask_bool(
        "Enable?",
        default=True
    ) is True


def test_boolean_default_false():

    wizard, _ = create_wizard([""])

    assert wizard.ask_bool(
        "Enable?",
        default=False
    ) is False


def test_boolean_invalid_input_retry():

    wizard, output = create_wizard(
        ["invalid", "yes"]
    )

    assert wizard.ask_bool("Enable?") is True

    assert any(
        "Please enter yes or no" in line
        for line in output
    )


def test_boolean_requires_explicit_response():

    wizard, output = create_wizard(
        ["", "no"]
    )

    assert wizard.ask_bool("Enable?") is False

    assert any(
        "Please enter yes or no" in line
        for line in output
    )


# ==================================================
# Primitive prompts
# ==================================================

def test_integer_prompt():

    wizard, _ = create_wizard(["25000"])

    schema = inspect_schema(ExampleConfig)[1]

    result = wizard.ask_primitive(schema)

    assert result == 25_000
    assert type(result) is int


def test_float_prompt():

    wizard, _ = create_wizard(["0.90"])

    schema = inspect_schema(ExampleConfig)[2]

    assert wizard.ask_primitive(schema) == 0.90


def test_integer_rejects_decimal():

    wizard, output = create_wizard(
        ["1.5", "2"]
    )

    schema = inspect_schema(ExampleConfig)[1]

    assert wizard.ask_primitive(schema) == 2

    assert any(
        "Invalid input" in line
        for line in output
    )


@pytest.mark.parametrize(
    "response",
    ["nan", "inf", "-inf"]
)
def test_nonfinite_number_rejected(response):

    wizard, output = create_wizard(
        [response, "100"]
    )

    schema = inspect_schema(ExampleConfig)[2]

    assert wizard.ask_primitive(schema) == 100

    assert any(
        "finite" in line
        for line in output
    )


def test_optional_primitive_none():

    wizard, _ = create_wizard(["none"])

    schema = inspect_schema(ExampleConfig)[5]

    assert wizard.ask_primitive(schema) is None


def test_optional_primitive_default():

    wizard, _ = create_wizard([""])

    schema = inspect_schema(ExampleConfig)[5]

    assert wizard.ask_primitive(schema) is None


def test_required_string_not_empty():

    wizard, output = create_wizard(
        ["", "Example"]
    )

    schema = inspect_schema(ExampleConfig)[0]

    assert (
        wizard.ask_primitive(schema)
        == "Example"
    )


# ==================================================
# Enum prompts
# ==================================================

def test_enum_number_selection():

    wizard, _ = create_wizard(["2"])

    schema = inspect_schema(ExampleConfig)[4]

    assert wizard.ask_enum(schema) == "second"


def test_enum_direct_value():

    wizard, _ = create_wizard(["first"])

    schema = inspect_schema(ExampleConfig)[4]

    assert wizard.ask_enum(schema) == "first"


def test_enum_invalid_selection():

    wizard, output = create_wizard(
        ["99", "1"]
    )

    schema = inspect_schema(ExampleConfig)[4]

    assert wizard.ask_enum(schema) == "first"

    assert any(
        "Invalid selection" in line
        for line in output
    )


def test_enum_default():

    @dataclass(frozen=True)
    class Config:

        mode: ExampleMode = ExampleMode.SECOND

    wizard, _ = create_wizard([""])

    schema = inspect_schema(Config)[0]

    assert wizard.ask_enum(schema) == "second"


# ==================================================
# Recursive configuration collection
# ==================================================

def test_collect_basic_configuration():

    wizard, _ = create_wizard([
        "Example",
        "10",
        "0.5",
        "yes",
        "1",
        "",
        "no"
    ])

    data = wizard.collect(ExampleConfig)

    assert data == {
        "name": "Example",
        "count": 10,
        "ratio": 0.5,
        "enabled": True,
        "mode": "first",
        "optional_value": None,
        "child": None
    }


def test_collect_nested_configuration():

    wizard, _ = create_wizard([
        "Example",
        "10",
        "0.5",
        "yes",
        "2",
        "100",
        "yes",
        "500"
    ])

    data = wizard.collect(ExampleConfig)

    assert data["optional_value"] == 100

    assert data["child"] == {
        "amount": 500
    }


def test_collect_drawdown_rules():

    wizard, _ = create_wizard([
        "2",       # Drawdown type
        "1000",    # Amount
        "25000",   # Trailing cap
        "",        # Default reference
        "",        # Default breach reference
        ""         # Default breach on equal
    ])

    data = wizard.collect(DrawdownRules)

    assert data["type"] == "trailing_eod"
    assert data["amount"] == 1000
    assert data["trailing_stop_balance"] == 25000
    assert data["reference"] == "balance"
    assert data["breach_reference"] == "equity"
    assert data["breach_on_equal"] is True


def test_unsupported_field_type():

    @dataclass
    class Config:

        values: list[int]

    wizard, _ = create_wizard([])

    with pytest.raises(
        TypeError,
        match="Unsupported field type"
    ):

        wizard.collect(Config)


# ==================================================
# Cancellation
# ==================================================

def test_cancel_command():

    wizard, _ = create_wizard(
        [":cancel"]
    )

    with pytest.raises(WizardCancelled):

        wizard.ask_bool("Continue?")


def test_keyboard_interrupt():

    def interrupted_input(prompt):

        raise KeyboardInterrupt

    wizard = ConfigWizard(
        input_fn=interrupted_input
    )

    with pytest.raises(WizardCancelled):

        wizard.ask_bool("Continue?")


def test_end_of_input():

    def closed_input(prompt):

        raise EOFError

    wizard = ConfigWizard(
        input_fn=closed_input
    )

    with pytest.raises(WizardCancelled):

        wizard.ask_bool("Continue?")


# ==================================================
# Verification
# ==================================================

def test_default_rules_unverified():

    rules = Ruleset(
        "Flex 25K",
        25_000
    )

    assert rules.rules_verified is False


def test_unverified_rejected_when_required():

    rules = Ruleset(
        "Flex 25K",
        25_000,

        evaluation=EvaluationRules(
            profit_target=1500,
            drawdown=DrawdownRules(
                DrawdownType.STATIC,
                1000
            ),
            fees=FeeRules()
        )
    )

    from openquant.propfirm.validation import (
        SimulationMode
    )

    result = rules.validate_for_simulation(
        SimulationMode.EVALUATION_ONLY,
        require_verified=True
    )

    assert not result.is_valid

    assert "rules_verified" in {
        error.path
        for error in result.errors
    }


def test_verified_evaluation_ready():

    rules = Ruleset(
        "Flex 25K",
        25_000,

        evaluation=EvaluationRules(
            profit_target=1500,
            drawdown=DrawdownRules(
                DrawdownType.STATIC,
                1000
            ),
            fees=FeeRules()
        ),

        rules_verified=True
    )

    from openquant.propfirm.validation import (
        SimulationMode
    )

    assert rules.validate_for_simulation(
        SimulationMode.EVALUATION_ONLY,
        require_verified=True
    ).is_valid


def test_verification_survives_serialization(firm):

    verified = replace(
        firm,

        ruleset=replace(
            firm.ruleset,
            rules_verified=True
        )
    )

    restored = PropFirm.from_json(
        verified.to_json()
    )

    assert restored.ruleset.rules_verified


def test_old_json_defaults_to_unverified(firm):

    data = firm.to_dict()

    del data["ruleset"]["rules_verified"]

    restored = PropFirm.from_dict(data)

    assert restored.ruleset.rules_verified is False


# ==================================================
# CLI listing and searching
# ==================================================

def test_list_empty_database(database):

    cli, output = create_cli(database)

    cli.list_firms()

    assert "No firms found." in output


def test_list_saved_firm(database, firm):

    database.add(firm)

    cli, output = create_cli(database)

    cli.list_firms()

    assert any(
        "Example Trading" in line
        for line in output
    )


def test_search_saved_firm(database, firm):

    database.add(firm)

    cli, output = create_cli(
        database,
        ["Example"]
    )

    cli.search_firms()

    assert any(
        "Flex 25K" in line
        for line in output
    )


def test_inspect_saved_firm(database, firm):

    firm_id = database.add(firm)

    cli, output = create_cli(
        database,
        [firm_id]
    )

    cli.inspect_firm()

    assert any(
        '"account_size": 25000' in line
        for line in output
    )


# ==================================================
# CLI firm creation
# ==================================================

def test_cli_add_firm(database):

    cli, output = create_cli(
        database,
        [
            "Example Trading",
            "Flex 25K",
            "25000",
            "no",       # Evaluation
            "no",       # Funded
            "no",       # Live
            "20",       # Maximum accounts
            "no",       # Rules verified
            "yes"       # Save
        ]
    )

    firm_id = cli.add_firm()

    assert firm_id == (
        "example_trading_flex_25k"
    )

    assert database.exists(firm_id)

    restored = database.get(firm_id)

    assert restored.ruleset.account_size == 25_000
    assert restored.ruleset.max_accounts == 20
    assert restored.ruleset.rules_verified is False


def test_cli_decline_save(database):

    cli, output = create_cli(
        database,
        [
            "Example Trading",
            "Flex 25K",
            "25000",
            "no",
            "no",
            "no",
            "20",
            "no",
            "no"
        ]
    )

    assert cli.add_firm() is None

    assert len(database) == 0


def test_cli_cancel_creation(database):

    cli, output = create_cli(
        database,
        [":cancel"]
    )

    assert cli.add_firm() is None

    assert len(database) == 0


def test_cli_invalid_firm_not_saved(database):

    cli, output = create_cli(
        database,
        [
            "Example Trading",
            "Flex 25K",
            "-25000",
            "no",
            "no",
            "no",
            "20",
            "no"
        ]
    )

    assert cli.add_firm() is None

    assert len(database) == 0

    assert any(
        "account_size" in line
        for line in output
    )


def test_cli_duplicate_not_saved(database, firm):

    database.add(firm)

    cli, output = create_cli(
        database,
        [
            "Example Trading",
            "Flex 25K",
            "25000",
            "no",
            "no",
            "no",
            "20",
            "no",
            "yes"
        ]
    )

    assert cli.add_firm() is None

    assert len(database) == 1

    assert any(
        "Duplicate account" in line
        for line in output
    )


# ==================================================
# Main menu
# ==================================================

def test_cli_exit(database):

    cli, output = create_cli(
        database,
        ["0"]
    )

    cli.run()
    assert "Exiting OpenQuant." in output


def test_cli_invalid_selection(database):

    cli, output = create_cli(
        database,
        ["invalid", "0"]
    )

    cli.run()
    assert "Invalid selection." in output


def test_cli_list_then_exit(database):

    cli, output = create_cli(
        database,
        ["2", "0"]
    )

    cli.run()
    assert "No firms found." in output
    assert "Exiting OpenQuant." in output


def test_cli_database_error(database):

    cli, output = create_cli(
        database,
        ["4", "missing_firm", "0"]
    )

    cli.run()
    assert any(
        "Database error" in line
        for line in output
    )


def test_cli_cancel_menu(database):

    cli, output = create_cli(
        database,
        [":cancel"]
    )
    cli.run()

    assert "Exiting OpenQuant." in output