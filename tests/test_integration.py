import pytest

from dataclasses import replace
from datetime import date

from openquant.propfirm.prop_firm import PropFirm

from openquant.propfirm.ruleset import (
    Ruleset,
    DrawdownRules,
    DrawdownType,
    FeeRules,
    EvaluationRules,
    FundedRules,
    PayoutRules,
    DailyLossRules
)

from openquant.propfirm.revision import (
    FirmRevision,
    RevisionMetadata,
    RuleSource,
    UnresolvedRule,
    UnresolvedReason
)

from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase
)

from openquant.propfirm.validation import (
    SimulationMode
)

from openquant.ui.editor import (
    ConfigEditor,
    EditorError,
    get_field_value,
    set_field_value,
    find_changes
)

from openquant.ui.wizard import (
    ConfigWizard,
    WizardCancelled
)

from openquant.ui.revision_cli import RevisionCLI

from openquant.ui.cli import OpenQuantCLI

from openquant.core.serialization import (
    DeserializationError
)


# ==================================================
# Fixtures
# ==================================================

@pytest.fixture
def firm():

    drawdown = DrawdownRules(
        DrawdownType.TRAILING_EOD,
        1000
    )

    return PropFirm(
        name="Example Trading",

        ruleset=Ruleset(
            account_name="Flex 25K",
            account_size=25_000,

            evaluation=EvaluationRules(
                profit_target=1500,
                drawdown=drawdown,
                fees=FeeRules(purchase=100)
            ),

            funded=FundedRules(
                drawdown=drawdown,

                fees=FeeRules(),

                payout=PayoutRules(
                    profit_split=0.90,
                    minimum_amount=250,
                    maximum_amount=1000
                )
            )
        )
    )


@pytest.fixture
def database(tmp_path):

    return VersionedFirmDatabase(
        tmp_path / "firms"
    )


@pytest.fixture
def wizard():

    return ConfigWizard(
        output_fn=lambda message: None
    )


@pytest.fixture
def editor(firm, wizard):

    return ConfigEditor(
        firm,

        wizard,

        readonly=(
            "name",
            "ruleset.account_name",
            "ruleset.account_size",
            "ruleset.rules_verified"
        )
    )


@pytest.fixture
def source():

    return RuleSource(
        title="Example official rules",
        url="https://example.com/rules",
        accessed_date=date.today().isoformat()
    )


def create_revision_cli(database, responses):

    responses = iter(responses)

    output = []

    wizard = ConfigWizard(
        input_fn=lambda prompt: next(responses),
        output_fn=output.append
    )

    return RevisionCLI(
        database,
        wizard
    ), output


def create_main_cli(database, responses):

    responses = iter(responses)

    output = []

    cli = OpenQuantCLI(
        database,

        input_fn=lambda prompt: next(responses),

        output_fn=output.append
    )

    return cli, output


# ==================================================
# Field path utilities
# ==================================================

def test_get_nested_field():

    data = {
        "evaluation": {
            "fees": {
                "purchase": 100
            }
        }
    }

    assert get_field_value(
        data,
        "evaluation.fees.purchase"
    ) == 100


def test_set_nested_field():

    data = {
        "evaluation": {
            "fees": {
                "purchase": 100
            }
        }
    }

    set_field_value(
        data,
        "evaluation.fees.purchase",
        80
    )

    assert data["evaluation"]["fees"]["purchase"] == 80


@pytest.mark.parametrize(
    "path",
    [
        "",
        "unknown",
        "evaluation.unknown",
        "evaluation.fees.purchase.invalid"
    ]
)
def test_invalid_field_path(path):

    data = {
        "evaluation": {
            "fees": {
                "purchase": 100
            }
        }
    }

    with pytest.raises(EditorError):

        get_field_value(data, path)

    with pytest.raises(EditorError):

        set_field_value(data, path, 80)


def test_find_changes():

    before = {
        "fees": {
            "purchase": 100,
            "reset": 75
        }
    }

    after = {
        "fees": {
            "purchase": 80,
            "reset": 75
        }
    }

    assert find_changes(
        before,
        after
    ) == {
        "fees.purchase": (100, 80)
    }


def test_find_changes_no_difference():

    data = {"amount": 100}

    assert find_changes(data, data) == {}


def test_find_changes_optional_configuration():

    before = {"daily_loss": None}

    after = {
        "daily_loss": {
            "amount": 500
        }
    }

    changes = find_changes(before, after)

    assert changes == {
        "daily_loss": (
            None,
            {"amount": 500}
        )
    }


# ==================================================
# Configuration editor
# ==================================================

def test_editor_initially_clean(editor):

    assert not editor.is_dirty
    assert editor.changes() == {}


def test_discover_nested_field(editor):

    paths = {
        field.path
        for field in editor.list_fields()
    }

    assert (
        "ruleset.evaluation.fees.purchase"
        in paths
    )


def test_discover_disabled_optional_rule(editor):

    field = editor.get_field(
        "ruleset.evaluation.daily_loss"
    )

    assert field.value is None
    assert field.schema.optional


def test_editor_modify_value(editor):

    editor.set_value(
        "ruleset.evaluation.fees.purchase",
        80
    )

    assert editor.is_dirty

    updated = editor.build()

    assert (
        updated.ruleset.evaluation.fees.purchase
        == 80
    )


def test_editor_preserves_original(
    editor,
    firm
):

    editor.set_value(
        "ruleset.evaluation.fees.purchase",
        80
    )

    assert (
        firm.ruleset.evaluation.fees.purchase
        == 100
    )


def test_editor_reset(editor):

    editor.set_value(
        "ruleset.evaluation.fees.purchase",
        80
    )

    editor.reset()

    assert not editor.is_dirty
    assert editor.changes() == {}


@pytest.mark.parametrize(
    "path,value",
    [
        ("name", "Different Firm"),
        ("ruleset.account_name", "Flex 50K"),
        ("ruleset.account_size", 50_000),
        ("ruleset.rules_verified", True),
        ("ruleset", {})
    ]
)
def test_readonly_fields(editor, path, value):

    with pytest.raises(
        EditorError,
        match="read-only"
    ):

        editor.set_value(path, value)


def test_editor_invalid_field(editor):

    with pytest.raises(EditorError):

        editor.set_value(
            "ruleset.unknown",
            100
        )


def test_editor_rejects_invalid_type(editor):

    editor.set_value(
        "ruleset.evaluation.profit_target",
        "invalid"
    )

    with pytest.raises(DeserializationError):

        editor.build()


def test_editor_detects_semantic_error(editor):

    editor.set_value(
        "ruleset.evaluation.profit_target",
        -1000
    )

    updated = editor.build()

    assert not updated.validate().is_valid


def test_editor_can_enable_optional_rule(editor):

    editor.set_value(
        "ruleset.evaluation.daily_loss",
        {"amount": 500}
    )

    updated = editor.build()

    assert isinstance(
        updated.ruleset.evaluation.daily_loss,
        DailyLossRules
    )

    assert (
        updated.ruleset.evaluation.daily_loss.amount
        == 500
    )


def test_editor_can_disable_optional_rule(editor):

    path = "ruleset.evaluation.daily_loss"

    editor.set_value(
        path,
        {"amount": 500}
    )

    editor.set_value(
        path,
        None
    )

    assert editor.build().ruleset.evaluation.daily_loss is None

    assert not editor.is_dirty


def test_editor_can_correct_intermediate_error(editor):

    editor.set_value(
        "ruleset.funded.payout.minimum_amount",
        1500
    )

    assert not editor.build().validate().is_valid

    editor.set_value(
        "ruleset.funded.payout.maximum_amount",
        2000
    )

    assert editor.build().validate().is_valid


def test_editor_interactive_default(firm):

    responses = iter([""])

    wizard = ConfigWizard(
        input_fn=lambda prompt: next(responses),
        output_fn=lambda message: None
    )

    editor = ConfigEditor(firm, wizard)

    editor.edit_field(
        "ruleset.evaluation.fees.purchase"
    )

    assert not editor.is_dirty


def test_editor_interactive_update(firm):

    responses = iter(["80"])

    wizard = ConfigWizard(
        input_fn=lambda prompt: next(responses),
        output_fn=lambda message: None
    )

    editor = ConfigEditor(firm, wizard)

    editor.edit_field(
        "ruleset.evaluation.fees.purchase"
    )

    assert editor.is_dirty


# ==================================================
# Revision CLI
# ==================================================

def test_show_revision_history(database, firm):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [firm_id]
    )

    cli.show_history()

    assert any(
        "Revision: 1" in line
        for line in output
    )


def test_inspect_latest_revision(database, firm):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [firm_id, ""]
    )

    cli.inspect_revision()

    assert any(
        '"revision": 1' in line
        for line in output
    )


@pytest.mark.parametrize(
    "revision",
    ["invalid", "0", "-1"]
)
def test_inspect_invalid_revision(
    database,
    firm,
    revision
):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [firm_id, revision]
    )

    cli.inspect_revision()

    assert any(
        "positive integer" in line
        for line in output
    )


def test_edit_revision_end_to_end(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [
            firm_id,
            "2",
            "ruleset.evaluation.fees.purchase",
            "80",
            "3",
            "6",
            "yes"
        ]
    )

    revision = cli.edit_revision()

    assert revision == 2

    original = database.get_revision(
        firm_id,
        1
    )

    latest = database.get_revision(
        firm_id
    )

    assert (
        original.firm.ruleset.evaluation.fees.purchase
        == 100
    )

    assert (
        latest.firm.ruleset.evaluation.fees.purchase
        == 80
    )

    assert original.fingerprint != latest.fingerprint


def test_cancel_edit_preserves_revision(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, _ = create_revision_cli(
        database,
        [
            firm_id,
            "2",
            "ruleset.evaluation.fees.purchase",
            "80",
            "0"
        ]
    )

    assert cli.edit_revision() is None

    assert database.list_revisions(firm_id) == [1]

    assert database.get(firm_id) == firm


def test_cannot_save_unchanged_revision(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [firm_id, "6", "0"]
    )

    cli.edit_revision()

    assert database.list_revisions(firm_id) == [1]

    assert "No changes to save." in output


def test_invalid_revision_not_saved(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, output = create_revision_cli(
        database,
        [
            firm_id,
            "2",
            "ruleset.evaluation.profit_target",
            "-1000",
            "6",
            "0"
        ]
    )

    cli.edit_revision()

    assert database.list_revisions(firm_id) == [1]

    assert any(
        "profit_target" in line
        for line in output
    )


# ==================================================
# Source and unresolved rule management
# ==================================================

def test_collect_source(database, source):

    cli, _ = create_revision_cli(
        database,
        [
            "yes",
            source.title,
            source.url,
            source.accessed_date,
            "no"
        ]
    )

    assert cli.collect_sources() == (source,)


def test_preserve_existing_sources(
    database,
    source
):

    cli, _ = create_revision_cli(
        database,
        [
            "no",  # Do not replace
            "no"   # Do not add
        ]
    )

    assert cli.collect_sources(
        (source,)
    ) == (source,)


def test_collect_unresolved_rule(database):

    cli, _ = create_revision_cli(
        database,
        [
            "yes",
            "funded.payout",
            "3",
            "Dynamic payout schedule",
            "no"
        ]
    )

    rules = cli.collect_unresolved_rules()

    assert len(rules) == 1

    assert (
        rules[0].reason
        is UnresolvedReason.UNSUPPORTED
    )


def test_preserve_existing_unresolved_rule(database):

    rule = UnresolvedRule(
        path="funded.payout",
        reason=UnresolvedReason.UNKNOWN,
        description="Requires research"
    )

    cli, _ = create_revision_cli(
        database,
        ["no", "no"]
    )

    assert cli.collect_unresolved_rules(
        (rule,)
    ) == (rule,)


# ==================================================
# Verification integration
# ==================================================

def test_verify_revision_end_to_end(
    database,
    firm,
    source
):

    firm_id = database.add(firm)

    today = date.today().isoformat()

    cli, output = create_revision_cli(
        database,
        [
            firm_id,

            "yes",  # Add source
            source.title,
            source.url,
            source.accessed_date,
            "no",   # No more sources

            "no",   # No unresolved rules

            today,  # Effective date

            "yes",  # Confirm verification

            today,  # Verified date

            "Reviewed all example rules.",

            "yes"   # Save
        ]
    )

    assert cli.verify_revision() == 2

    latest = database.get_revision(firm_id)

    assert latest.firm.ruleset.rules_verified

    assert latest.metadata.sources == (source,)

    assert latest.metadata.verified_date == today

    assert latest.validate_for_simulation().is_valid

    original = database.get_revision(firm_id, 1)

    assert not original.firm.ruleset.rules_verified

    assert (
        original.rules_fingerprint
        == latest.rules_fingerprint
    )


def test_verification_without_sources_fails(
    database,
    firm
):

    firm_id = database.add(firm)

    today = date.today().isoformat()

    cli, output = create_revision_cli(
        database,
        [
            firm_id,
            "no",   # No sources
            "no",   # No unresolved rules
            today,
            "yes",  # Attempt verification
            today,
            ""
        ]
    )

    assert cli.verify_revision() is None

    assert database.list_revisions(firm_id) == [1]

    assert any(
        "metadata.sources" in line
        for line in output
    )


def test_unsupported_rule_blocks_hypothetical_simulation(
    firm
):

    record = FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            unresolved_rules=(
                UnresolvedRule(
                    path="funded.trading.scaling",
                    reason=UnresolvedReason.UNSUPPORTED,
                    description="Unsupported scaling"
                ),
            )
        )
    )

    assert record.validate().is_valid

    result = record.validate_for_simulation(
        require_verified=False
    )

    assert not result.is_valid


def test_unknown_rule_allows_conditional_research(
    firm
):

    record = FirmRevision(
        firm=firm,

        metadata=RevisionMetadata(
            unresolved_rules=(
                UnresolvedRule(
                    path="evaluation.fees.purchase",
                    reason=UnresolvedReason.UNKNOWN,
                    description="Hypothetical purchase price"
                ),
            )
        )
    )

    result = record.validate_for_simulation(
        require_verified=False
    )

    assert result.is_valid
    assert result.warnings


# ==================================================
# Main application integration
# ==================================================

def test_main_cli_revision_history(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, output = create_main_cli(
        database,
        ["6", firm_id, "0"]
    )

    cli.run()

    assert any(
        "Revision: 1" in line
        for line in output
    )


def test_main_cli_checks_unsupported_rules(
    database,
    firm
):

    firm_id = database.add(
        firm,

        RevisionMetadata(
            unresolved_rules=(
                UnresolvedRule(
                    path="funded.payout",
                    reason=UnresolvedReason.UNSUPPORTED,
                    description="Unsupported payout schedule"
                ),
            )
        )
    )

    cli, output = create_main_cli(
        database,
        ["5", firm_id, "0"]
    )

    cli.run()

    assert any(
        "Unsupported rule behavior" in line
        for line in output
    )


def test_main_cli_cancel_revision_edit(
    database,
    firm
):

    firm_id = database.add(firm)

    cli, output = create_main_cli(
        database,
        ["8", firm_id, ":cancel", "0"]
    )

    cli.run()

    assert database.list_revisions(firm_id) == [1]

    assert "Operation cancelled." in output


def test_full_persistence_workflow(
    database,
    firm,
    wizard
):

    firm_id = database.add(firm)

    editor = ConfigEditor(
        database.get(firm_id),
        wizard
    )

    editor.set_value(
        "ruleset.evaluation.fees.purchase",
        80
    )

    updated = editor.build()

    database.add_revision(
        firm_id,
        updated,
        expected_revision=1
    )

    reopened = VersionedFirmDatabase(
        database.directory
    )

    assert reopened.get(firm_id) == updated

    assert reopened.get_revision(
        firm_id,
        1
    ).firm == firm

    assert reopened.list_revisions(firm_id) == [
        1, 2
    ]

def test_editor_populates_nested_defaults(editor):

    path = "ruleset.evaluation.daily_loss"

    editor.set_value(
        path,
        {"amount": 500}
    )

    data = get_field_value(
        editor.data,
        path
    )

    assert data == {
        "amount": 500.0,
        "action": "terminate",
        "reference": "equity",
        "breach_on_equal": True
    }


def test_editor_discovers_new_nested_fields(editor):

    path = "ruleset.evaluation.daily_loss"

    editor.set_value(
        path,
        {"amount": 500}
    )

    paths = {
        field.path
        for field in editor.list_fields()
    }

    assert f"{path}.amount" in paths
    assert f"{path}.action" in paths
    assert f"{path}.reference" in paths
    assert f"{path}.breach_on_equal" in paths


def test_editor_can_modify_new_nested_field(editor):

    path = "ruleset.evaluation.daily_loss"

    editor.set_value(
        path,
        {"amount": 500}
    )

    editor.set_value(
        f"{path}.amount",
        750
    )

    updated = editor.build()

    assert (
        updated.ruleset.evaluation.daily_loss.amount
        == 750
    )


def test_editor_repeated_optional_rule_toggle(editor):

    path = "ruleset.evaluation.daily_loss"

    for _ in range(5):

        editor.set_value(
            path,
            {"amount": 500}
        )

        assert (
            editor.build()
            .ruleset.evaluation.daily_loss
            .amount == 500
        )

        editor.set_value(
            path,
            None
        )

        assert (
            editor.build()
            .ruleset.evaluation.daily_loss
            is None
        )

    assert not editor.is_dirty


def test_editor_rejects_incomplete_nested_rule(editor):

    path = "ruleset.evaluation.daily_loss"

    with pytest.raises(DeserializationError):

        editor.set_value(
            path,
            {}
        )

    assert not editor.is_dirty

    assert editor.get_field(path).value is None