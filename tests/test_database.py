import pytest

from dataclasses import replace

from openquant.core.serialization import (
    SerializationError
)

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownType,
    DrawdownRules,
    FeeRules,
    EvaluationRules,
    FundedRules,
    PayoutRules
)

from openquant.propfirm.database import (
    FirmDatabase,
    FirmAlreadyExistsError,
    FirmNotFoundError,
    InvalidFirmIDError,
    DatabaseEntryError,
    normalize_identifier,
    generate_firm_id,
    validate_firm_id,
    atomic_create
)


# --------------------------------------------------
# Fixtures
# --------------------------------------------------

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
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,

                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000
                ),

                fees=FeeRules(
                    purchase=100
                )
            ),

            funded=FundedRules(
                drawdown=DrawdownRules(
                    DrawdownType.TRAILING_EOD,
                    1000
                ),

                fees=FeeRules(),

                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250
                )
            )
        )
    )


@pytest.fixture
def multiple_firms(firm):

    firm_50k = replace(
        firm,
        ruleset=replace(
            firm.ruleset,
            account_name="Flex 50K",
            account_size=50_000
        )
    )

    instant_firm = replace(
        firm,
        name="Sample Funding",
        ruleset=replace(
            firm.ruleset,
            account_name="Instant 25K",
            evaluation=None
        )
    )

    return [
        firm,
        firm_50k,
        instant_firm
    ]


@pytest.fixture
def populated_database(
    database,
    multiple_firms
):

    for firm in multiple_firms:

        database.add(firm)

    return database


# --------------------------------------------------
# Database initialization
# --------------------------------------------------

def test_create_database_directory(tmp_path):

    path = tmp_path / "new_database"

    assert not path.exists()

    database = FirmDatabase(path)

    assert database.directory.exists()
    assert database.directory.is_dir()


def test_nested_directory_creation(tmp_path):

    path = (
        tmp_path
        / "research"
        / "database"
        / "firms"
    )

    database = FirmDatabase(path)

    assert database.directory.exists()


def test_database_uses_absolute_path(database):

    assert database.directory.is_absolute()


def test_empty_database(database):

    assert database.list_ids() == []
    assert database.list_all() == []
    assert database.load_all() == {}
    assert len(database) == 0


# --------------------------------------------------
# Identifier normalization
# --------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("Example Trading", "example_trading"),
        ("Flex 25K", "flex_25k"),
        ("  Flex 25K  ", "flex_25k"),
        ("FLEX-25K!", "flex_25k"),
        ("Flex___25K", "flex_25k"),
        ("Café Trading", "cafe_trading"),
        ("25K", "25k"),
        ("Firm / Account", "firm_account")
    ]
)
def test_normalize_identifier(value, expected):

    assert normalize_identifier(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "!!!",
        "___",
        None,
        123
    ]
)
def test_invalid_normalization(value):

    with pytest.raises(InvalidFirmIDError):

        normalize_identifier(value)


def test_generate_firm_id(firm):

    assert generate_firm_id(firm) == (
        "example_trading_flex_25k"
    )


def test_identifier_deterministic(firm):

    first = generate_firm_id(firm)
    second = generate_firm_id(firm)

    assert first == second


@pytest.mark.parametrize(
    "firm_id",
    [
        "example_trading",
        "flex_25k",
        "25k",
        "firm"
    ]
)
def test_valid_firm_id(firm_id):

    validate_firm_id(firm_id)


@pytest.mark.parametrize(
    "firm_id",
    [
        "",
        "../example",
        "../../example",
        "/tmp/example",
        "example.json",
        "Example_Firm",
        "example firm",
        "example__firm",
        "_example",
        "example_",
        "example\\firm",
        None
    ]
)
def test_invalid_firm_id(firm_id):

    with pytest.raises(InvalidFirmIDError):

        validate_firm_id(firm_id)


# --------------------------------------------------
# Add and retrieve
# --------------------------------------------------

def test_add_firm(database, firm):

    firm_id = database.add(firm)

    assert firm_id == (
        "example_trading_flex_25k"
    )

    path = (
        database.directory
        / f"{firm_id}.json"
    )

    assert path.exists()


def test_get_firm(database, firm):

    firm_id = database.add(firm)

    restored = database.get(firm_id)

    assert restored == firm
    assert restored is not firm


def test_nested_types_preserved(database, firm):

    firm_id = database.add(firm)

    restored = database.get(firm_id)

    assert (
        restored.ruleset.evaluation.drawdown.type
        is DrawdownType.TRAILING_EOD
    )


def test_firm_exists(database, firm):

    firm_id = generate_firm_id(firm)

    assert not database.exists(firm_id)

    database.add(firm)

    assert database.exists(firm_id)


def test_missing_firm(database):

    with pytest.raises(FirmNotFoundError):

        database.get("missing_firm")


def test_duplicate_firm(database, firm):

    database.add(firm)

    with pytest.raises(FirmAlreadyExistsError):

        database.add(firm)


def test_duplicate_preserves_original(
    database,
    firm
):

    firm_id = database.add(firm)

    path = (
        database.directory
        / f"{firm_id}.json"
    )

    original_content = path.read_bytes()

    modified = replace(
        firm,

        ruleset=replace(
            firm.ruleset,
            max_accounts=10
        )
    )

    with pytest.raises(FirmAlreadyExistsError):

        database.add(modified)

    assert path.read_bytes() == original_content

    assert database.get(firm_id) == firm


def test_normalized_id_collision(database, firm):

    database.add(firm)

    conflicting = replace(
        firm,
        name="Example-Trading"
    )

    with pytest.raises(FirmAlreadyExistsError):

        database.add(conflicting)


def test_invalid_firm_not_added(database, firm):

    invalid = replace(
        firm,
        name=""
    )

    with pytest.raises(SerializationError):

        database.add(invalid)

    assert len(database) == 0


def test_add_requires_prop_firm(database):

    with pytest.raises(TypeError):

        database.add({})


# --------------------------------------------------
# Persistence
# --------------------------------------------------

def test_database_persists_between_instances(
    database,
    firm
):

    firm_id = database.add(firm)

    reopened = FirmDatabase(
        database.directory
    )

    assert reopened.exists(firm_id)
    assert reopened.get(firm_id) == firm


def test_external_file_discovered(database, firm):

    firm_id = generate_firm_id(firm)

    path = (
        database.directory
        / f"{firm_id}.json"
    )

    firm.save(path)

    assert firm_id in database.list_ids()
    assert database.get(firm_id) == firm


# --------------------------------------------------
# Database discovery
# --------------------------------------------------

def test_list_ids(populated_database):

    identifiers = populated_database.list_ids()

    assert identifiers == [
        "example_trading_flex_25k",
        "example_trading_flex_50k",
        "sample_funding_instant_25k"
    ]


def test_load_all(populated_database):

    firms = populated_database.load_all()

    assert len(firms) == 3

    assert all(
        isinstance(firm, PropFirm)
        for firm in firms.values()
    )


def test_list_all(populated_database):

    firms = populated_database.list_all()

    assert len(firms) == 3

    assert all(
        isinstance(firm, PropFirm)
        for firm in firms
    )


def test_non_json_files_ignored(database):

    (database.directory / "README.md").write_text(
        "Database documentation"
    )

    (database.directory / ".firm_test.tmp").write_text(
        "Incomplete temporary file"
    )

    assert database.list_ids() == []


def test_database_length(populated_database):

    assert len(populated_database) == 3


def test_database_membership(
    populated_database
):

    assert (
        "example_trading_flex_25k"
        in populated_database
    )

    assert (
        "missing_firm"
        not in populated_database
    )


# --------------------------------------------------
# Searching
# --------------------------------------------------

def test_search_firm_name(populated_database):

    results = populated_database.search(
        "Example"
    )

    assert len(results) == 2


def test_search_account_name(populated_database):

    results = populated_database.search(
        "Flex 25K"
    )

    assert list(results) == [
        "example_trading_flex_25k"
    ]


def test_search_case_insensitive(
    populated_database
):

    results = populated_database.search(
        "EXAMPLE TRADING"
    )

    assert len(results) == 2


def test_search_with_whitespace(
    populated_database
):

    results = populated_database.search(
        "  instant  "
    )

    assert len(results) == 1


def test_search_no_results(populated_database):

    results = populated_database.search(
        "nonexistent"
    )

    assert results == {}


def test_empty_search_returns_all(
    populated_database
):

    assert (
        populated_database.search("")
        == populated_database.load_all()
    )


def test_search_requires_string(database):

    with pytest.raises(TypeError):

        database.search(123)


# --------------------------------------------------
# Filtering
# --------------------------------------------------

def test_filter_account_size(
    populated_database
):

    results = populated_database.filter(
        lambda firm:
            firm.ruleset.account_size == 25_000
    )

    assert len(results) == 2


def test_filter_evaluation_accounts(
    populated_database
):

    results = populated_database.filter(
        lambda firm:
            firm.ruleset.evaluation is not None
    )

    assert len(results) == 2


def test_filter_instant_funded(
    populated_database
):

    results = populated_database.filter(
        lambda firm:
            firm.ruleset.evaluation is None
            and firm.ruleset.funded is not None
    )

    assert list(results) == [
        "sample_funding_instant_25k"
    ]


def test_filter_simulation_ready(
    populated_database
):

    incomplete = PropFirm(
        "Incomplete Firm",
        Ruleset("25K", 25_000)
    )

    populated_database.add(incomplete)

    results = populated_database.filter(
        lambda firm:
            firm.ruleset
            .validate_for_simulation()
            .is_valid
    )

    assert len(results) == 3

    assert (
        "incomplete_firm_25k"
        not in results
    )


def test_filter_no_results(populated_database):

    results = populated_database.filter(
        lambda firm: False
    )

    assert results == {}


def test_filter_requires_callable(database):

    with pytest.raises(TypeError):

        database.filter(None)


# --------------------------------------------------
# Invalid and corrupted database entries
# --------------------------------------------------

def test_invalid_json_entry(database):

    path = (
        database.directory
        / "corrupted.json"
    )

    path.write_text("{invalid")

    with pytest.raises(DatabaseEntryError):

        database.get("corrupted")


def test_mismatched_identifier(database, firm):

    path = (
        database.directory
        / "incorrect_name.json"
    )

    firm.save(path)

    with pytest.raises(
        DatabaseEntryError,
        match="Identifier mismatch"
    ):

        database.get("incorrect_name")


def test_invalid_database_filename(database):

    path = (
        database.directory
        / "Invalid Name.json"
    )

    path.write_text("{}")

    with pytest.raises(DatabaseEntryError):

        database.list_ids()


def test_load_all_does_not_skip_corruption(
    database,
    firm
):

    database.add(firm)

    path = (
        database.directory
        / "corrupted.json"
    )

    path.write_text("{invalid")

    with pytest.raises(DatabaseEntryError):

        database.load_all()


def test_path_traversal_rejected(database):

    with pytest.raises(InvalidFirmIDError):

        database.get("../outside")


def test_symbolic_link_rejected(
    database,
    firm,
    tmp_path
):

    external = tmp_path / "external.json"

    firm.save(external)

    firm_id = generate_firm_id(firm)

    link = (
        database.directory
        / f"{firm_id}.json"
    )

    link.symlink_to(external)

    with pytest.raises(DatabaseEntryError):

        database.get(firm_id)


# --------------------------------------------------
# Atomic file creation
# --------------------------------------------------

def test_atomic_create(tmp_path):

    path = tmp_path / "entry.json"

    content = '{"name": "example"}'

    atomic_create(path, content)

    assert path.read_text() == content


def test_atomic_create_does_not_overwrite(
    tmp_path
):

    path = tmp_path / "entry.json"

    atomic_create(path, "original")

    with pytest.raises(FileExistsError):

        atomic_create(path, "replacement")

    assert path.read_text() == "original"


def test_temporary_file_removed_after_success(
    tmp_path
):

    path = tmp_path / "entry.json"

    atomic_create(path, "{}")

    assert list(
        tmp_path.glob(".firm_*.tmp")
    ) == []


def test_temporary_file_removed_after_duplicate(
    tmp_path
):

    path = tmp_path / "entry.json"

    atomic_create(path, "{}")

    with pytest.raises(FileExistsError):

        atomic_create(path, "{}")

    assert list(
        tmp_path.glob(".firm_*.tmp")
    ) == []


def test_failed_publication_cleanup(
    tmp_path,
    monkeypatch
):

    path = tmp_path / "entry.json"

    def failed_link(*args, **kwargs):

        raise OSError(
            "Simulated publication failure"
        )

    monkeypatch.setattr(
        "openquant.propfirm.database.os.link",
        failed_link
    )

    with pytest.raises(OSError):

        atomic_create(path, "{}")

    assert not path.exists()

    assert list(
        tmp_path.glob(".firm_*.tmp")
    ) == []


def test_failed_write_does_not_publish(
    tmp_path,
    monkeypatch
):

    path = tmp_path / "entry.json"

    def failed_fsync(*args, **kwargs):

        raise OSError(
            "Simulated synchronization failure"
        )

    monkeypatch.setattr(
        "openquant.propfirm.database.os.fsync",
        failed_fsync
    )

    with pytest.raises(OSError):

        atomic_create(path, "{}")

    assert not path.exists()

    assert list(
        tmp_path.glob(".firm_*.tmp")
    ) == []