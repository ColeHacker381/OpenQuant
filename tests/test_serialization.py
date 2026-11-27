import json
import pytest

from dataclasses import dataclass, replace

from openquant.core.serialization import (
    SerializationError,
    DeserializationError,
    serialize,
    deserialize,
    to_dict,
    from_dict,
    to_json,
    from_json,
    save_json,
    load_json
)

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    BalanceReference,
    DrawdownRules,
    FeeRules,
    DailyLossRules,
    ConsistencyRules,
    ConsistencyWindow,
    TradingRules,
    PayoutRules,
    LiveTransitionRules,
    EvaluationRules,
    FundedRules,
    LiveRules
)


# --------------------------------------------------
# Fixtures and generic test configurations
# --------------------------------------------------

@pytest.fixture
def firm():

    evaluation = EvaluationRules(
        profit_target=1500,
        drawdown=DrawdownRules(
            DrawdownType.TRAILING_EOD,
            1000
        ),
        fees=FeeRules(
            purchase=100,
            reset=75
        ),
        minimum_trading_days=3,
        daily_loss=DailyLossRules(500),
        consistency=ConsistencyRules(0.40),
        trading=TradingRules(
            max_mini_contracts=2,
            max_micro_contracts=20
        )
    )

    funded = FundedRules(
        drawdown=DrawdownRules(
            DrawdownType.TRAILING_INTRADAY,
            1000,
            trailing_stop_balance=25_000,
            reference=BalanceReference.EQUITY
        ),
        fees=FeeRules(activation=50),
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=250,
            maximum_amount=1000,
            minimum_profitable_days=5,
            profitable_day_threshold=100,
            consistency=ConsistencyRules(
                maximum_ratio=0.30,
                window=ConsistencyWindow.SINCE_LAST_PAYOUT
            )
        ),
        live_transition=LiveTransitionRules(
            minimum_payout_count=5
        )
    )

    live = LiveRules(
        drawdown=DrawdownRules(
            DrawdownType.STATIC,
            2000
        ),
        fees=FeeRules(),
        payout=PayoutRules(
            profit_split=0.90,
            minimum_amount=100
        ),
        starting_balance=25_000
    )

    return PropFirm(
        name="Example Trading",
        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,
            evaluation=evaluation,
            funded=funded,
            live=live,
            max_accounts=20
        )
    )


@dataclass(frozen=True)
class ExampleConfig:
    name: str
    values: tuple[int, ...]
    parameters: dict[str, float]
    optional: float | None = None


# --------------------------------------------------
# Primitive serialization
# --------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [None, True, False, 100, 1.5, "example"]
)
def test_serialize_primitive(value):

    assert serialize(value) == value


def test_serialize_enum():

    assert (
        serialize(DrawdownType.TRAILING_EOD)
        == "trailing_eod"
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")]
)
def test_serialize_nonfinite(value):

    with pytest.raises(SerializationError):
        serialize(value)


def test_serialize_unsupported_type():

    with pytest.raises(SerializationError):
        serialize(object())


def test_serialize_invalid_dictionary_key():

    with pytest.raises(SerializationError):
        serialize({1: "invalid"})


# --------------------------------------------------
# Primitive deserialization
# --------------------------------------------------

def test_deserialize_integer():

    assert deserialize(100, int) == 100


def test_deserialize_float():

    result = deserialize(100, float)

    assert result == 100.0
    assert type(result) is float


def test_deserialize_boolean():

    assert deserialize(True, bool) is True


@pytest.mark.parametrize(
    "value,expected_type",
    [
        (True, int),
        (False, float),
        ("1000", float),
        ("1000", int),
        (1.5, int),
        ("false", bool),
        (1, bool),
        (123, str)
    ]
)
def test_reject_invalid_primitive(value, expected_type):

    with pytest.raises(DeserializationError):
        deserialize(value, expected_type)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")]
)
def test_deserialize_nonfinite(value):

    with pytest.raises(DeserializationError):
        deserialize(value, float)


def test_deserialize_enum():

    result = deserialize(
        "trailing_eod",
        DrawdownType
    )

    assert result is DrawdownType.TRAILING_EOD


def test_deserialize_invalid_enum():

    with pytest.raises(
        DeserializationError,
        match="Invalid value"
    ):

        deserialize("invalid", DrawdownType)


def test_deserialize_optional_none():

    assert deserialize(None, float | None) is None


def test_deserialize_optional_value():

    assert deserialize(100, float | None) == 100.0


# --------------------------------------------------
# Dataclass reconstruction
# --------------------------------------------------

def test_drawdown_round_trip():

    original = DrawdownRules(
        DrawdownType.TRAILING_EOD,
        1000,
        trailing_stop_balance=25_000
    )

    restored = from_dict(
        DrawdownRules,
        to_dict(original)
    )

    assert restored == original
    assert restored is not original
    assert restored.type is DrawdownType.TRAILING_EOD


def test_missing_required_field():

    with pytest.raises(
        DeserializationError,
        match="amount: Required field is missing"
    ):

        from_dict(
            DrawdownRules,
            {"type": "static"}
        )


def test_default_fields_applied():

    result = from_dict(
        DrawdownRules,
        {
            "type": "static",
            "amount": 1000
        }
    )

    assert result.trailing_stop_balance is None
    assert result.breach_on_equal is True
    assert result.reference is BalanceReference.BALANCE


def test_unknown_field_rejected():

    with pytest.raises(
        DeserializationError,
        match="Unknown fields: ammount"
    ):

        from_dict(
            DrawdownRules,
            {
                "type": "static",
                "amount": 1000,
                "ammount": 2000
            }
        )


def test_required_field_cannot_be_null():

    with pytest.raises(DeserializationError):

        from_dict(
            DrawdownRules,
            {
                "type": "static",
                "amount": None
            }
        )


def test_invalid_root_object():

    with pytest.raises(DeserializationError):

        from_dict(Ruleset, [])


# --------------------------------------------------
# Collection support
# --------------------------------------------------

def test_list_deserialization():

    assert deserialize(
        [1, 2, 3],
        list[int]
    ) == [1, 2, 3]


def test_tuple_deserialization():

    assert deserialize(
        [1, 2, 3],
        tuple[int, ...]
    ) == (1, 2, 3)


def test_fixed_tuple_deserialization():

    assert deserialize(
        [1, "hello"],
        tuple[int, str]
    ) == (1, "hello")


def test_invalid_tuple_length():

    with pytest.raises(DeserializationError):

        deserialize(
            [1],
            tuple[int, str]
        )


def test_dictionary_deserialization():

    result = deserialize(
        {"risk": 100},
        dict[str, float]
    )

    assert result == {"risk": 100.0}
    assert type(result["risk"]) is float


def test_collection_error_path():

    with pytest.raises(DeserializationError) as exc:

        deserialize(
            [1, "invalid", 3],
            list[int],
            "values"
        )

    assert exc.value.path == "values[1]"


def test_generic_configuration_round_trip():

    original = ExampleConfig(
        name="Strategy Config",
        values=(1, 2, 3),
        parameters={"risk": 100.0}
    )

    restored = from_json(
        ExampleConfig,
        to_json(original)
    )

    assert restored == original
    assert type(restored.values) is tuple


# --------------------------------------------------
# Prop firm serialization
# --------------------------------------------------

def test_firm_to_dict(firm):

    data = firm.to_dict()

    assert data["name"] == "Example Trading"
    assert data["ruleset"]["account_size"] == 25_000

    assert (
        data["ruleset"]["evaluation"]["drawdown"]["type"]
        == "trailing_eod"
    )


def test_firm_from_dict(firm):

    restored = PropFirm.from_dict(
        firm.to_dict()
    )

    assert restored == firm
    assert restored is not firm
    assert restored.ruleset is not firm.ruleset


def test_nested_types_preserved(firm):

    restored = PropFirm.from_dict(
        firm.to_dict()
    )

    assert isinstance(restored.ruleset, Ruleset)

    assert isinstance(
        restored.ruleset.evaluation,
        EvaluationRules
    )

    assert isinstance(
        restored.ruleset.funded.payout,
        PayoutRules
    )

    assert (
        restored.ruleset.evaluation.drawdown.type
        is DrawdownType.TRAILING_EOD
    )

    assert (
        restored.ruleset.funded.payout.consistency.window
        is ConsistencyWindow.SINCE_LAST_PAYOUT
    )


def test_firm_json_round_trip(firm):

    restored = PropFirm.from_json(
        firm.to_json()
    )

    assert restored == firm
    assert restored.validate().is_valid


def test_incomplete_ruleset_round_trip():

    original = PropFirm(
        "Example Firm",
        Ruleset("25K", 25_000)
    )

    restored = PropFirm.from_json(
        original.to_json()
    )

    assert restored == original
    assert restored.validate().is_valid

    assert not (
        restored.ruleset
        .validate_for_simulation()
        .is_valid
    )


def test_nested_error_path(firm):

    data = firm.to_dict()

    data["ruleset"]["evaluation"]["drawdown"]["amount"] = (
        "invalid"
    )

    with pytest.raises(DeserializationError) as exc:

        PropFirm.from_dict(data)

    assert exc.value.path == (
        "ruleset.evaluation.drawdown.amount"
    )


# --------------------------------------------------
# Validation integration
# --------------------------------------------------

def test_empty_firm_name(firm):

    invalid = replace(firm, name="")

    assert not invalid.validate().is_valid


def test_invalid_firm_cannot_serialize(firm):

    invalid = replace(firm, name="")

    with pytest.raises(SerializationError):
        invalid.to_json()


def test_invalid_ruleset_cannot_serialize(firm):

    invalid = replace(
        firm,
        ruleset=replace(
            firm.ruleset,
            account_size=-1000
        )
    )

    with pytest.raises(SerializationError):
        invalid.to_dict()


def test_invalid_semantic_value_rejected(firm):

    data = firm.to_dict()

    data["ruleset"]["account_size"] = -1000

    with pytest.raises(
        DeserializationError,
        match="ruleset.account_size"
    ):

        PropFirm.from_dict(data)


# --------------------------------------------------
# JSON parsing
# --------------------------------------------------

def test_invalid_json():

    with pytest.raises(
        DeserializationError,
        match="Invalid JSON"
    ):

        PropFirm.from_json("{invalid")


def test_duplicate_json_key():

    content = (
        '{"account_name": "25K", '
        '"account_size": 25000, '
        '"account_size": 50000}'
    )

    with pytest.raises(
        DeserializationError,
        match="Duplicate key"
    ):

        from_json(Ruleset, content)


@pytest.mark.parametrize(
    "constant",
    ["NaN", "Infinity", "-Infinity"]
)
def test_invalid_json_constant(constant):

    content = (
        '{"type": "static", '
        f'"amount": {constant}'
        '}'
    )

    with pytest.raises(
        DeserializationError,
        match="Invalid numeric constant"
    ):

        from_json(DrawdownRules, content)


def test_json_output_is_valid(firm):

    data = json.loads(firm.to_json())

    assert isinstance(data, dict)
    assert data["name"] == firm.name


# --------------------------------------------------
# File persistence
# --------------------------------------------------

def test_save_and_load_firm(firm, tmp_path):

    path = tmp_path / "example.json"

    firm.save(path)

    assert path.exists()

    restored = PropFirm.load(path)

    assert restored == firm


def test_generic_file_round_trip(tmp_path):

    path = tmp_path / "config.json"

    original = ExampleConfig(
        name="Example",
        values=(1, 2),
        parameters={"risk": 100}
    )

    save_json(original, path)

    restored = load_json(
        ExampleConfig,
        path
    )

    assert restored == original


def test_existing_file_not_overwritten(firm, tmp_path):

    path = tmp_path / "example.json"

    firm.save(path)

    original_content = path.read_text()

    with pytest.raises(FileExistsError):
        firm.save(path)

    assert path.read_text() == original_content


def test_missing_file(tmp_path):

    with pytest.raises(FileNotFoundError):

        PropFirm.load(
            tmp_path / "missing.json"
        )


def test_invalid_firm_not_saved(firm, tmp_path):

    path = tmp_path / "invalid.json"

    invalid = replace(firm, name="")

    with pytest.raises(SerializationError):
        invalid.save(path)

    assert not path.exists()


def test_corrupted_file(tmp_path):

    path = tmp_path / "corrupted.json"

    path.write_text("{invalid")

    with pytest.raises(DeserializationError):
        PropFirm.load(path)