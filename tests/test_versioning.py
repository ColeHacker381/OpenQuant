import json
import pytest

from dataclasses import replace
from datetime import date

from openquant.core.serialization import (
    SerializationError,
    DeserializationError
)

from openquant.propfirm.database import (
    FirmDatabase,
    FirmAlreadyExistsError,
    FirmNotFoundError,
    DatabaseEntryError,
    DatabaseError
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

from openquant.propfirm.revision import (
    SCHEMA_VERSION,
    RuleSource,
    UnresolvedReason,
    UnresolvedRule,
    RevisionMetadata,
    FirmRevision,
    validate_metadata
)

from openquant.propfirm.versioned_database import (
    VersionedFirmDatabase,
    RevisionNotFoundError,
    RevisionConflictError
)


# ==================================================
# Fixtures and helpers
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
                    minimum_amount=250
                )
            ),

            max_accounts=20
        )
    )


@pytest.fixture
def metadata():

    return RevisionMetadata(
        effective_date="2026-01-01",
        notes="Initial configuration"
    )


@pytest.fixture
def record(firm, metadata):

    return FirmRevision(
        firm=firm,
        metadata=metadata
    )


@pytest.fixture
def source():

    return RuleSource(
        title="Example evaluation rules",
        url="https://example.com/rules",
        accessed_date="2026-01-01"
    )


@pytest.fixture
def database(tmp_path):

    return VersionedFirmDatabase(
        tmp_path / "firm_database"
    )


@pytest.fixture
def populated_database(database, firm, metadata):

    database.add(firm, metadata)

    return database


def update_fee(firm, fee):

    return replace(
        firm,

        ruleset=replace(
            firm.ruleset,

            evaluation=replace(
                firm.ruleset.evaluation,

                fees=replace(
                    firm.ruleset.evaluation.fees,
                    purchase=fee
                )
            )
        )
    )


def mark_verified(firm):

    return replace(
        firm,

        ruleset=replace(
            firm.ruleset,
            rules_verified=True
        )
    )


def get_error_paths(result):

    return {
        error.path
        for error in result.errors
    }


# ==================================================
# Metadata validation
# ==================================================

def test_default_metadata():

    metadata = RevisionMetadata()

    assert metadata.schema_version == SCHEMA_VERSION
    assert metadata.revision == 1
    assert metadata.effective_date is None
    assert metadata.verified_date is None
    assert metadata.sources == ()
    assert metadata.unresolved_rules == ()

    assert validate_metadata(metadata).is_valid


@pytest.mark.parametrize(
    "value",
    [0, -1, True, "1"]
)
def test_invalid_revision_number(value):

    metadata = RevisionMetadata(
        revision=value
    )

    assert not validate_metadata(metadata).is_valid


def test_unsupported_schema_version():

    metadata = RevisionMetadata(
        schema_version=999
    )

    result = validate_metadata(metadata)

    assert "schema_version" in get_error_paths(result)


@pytest.mark.parametrize(
    "value",
    [
        "invalid",
        "2026-02-30",
        "01-01-2026",
        "20260101"
    ]
)
def test_invalid_effective_date(value):

    metadata = RevisionMetadata(
        effective_date=value
    )

    result = validate_metadata(metadata)

    assert "effective_date" in get_error_paths(result)


def test_future_effective_date_allowed():

    metadata = RevisionMetadata(
        effective_date="2099-01-01"
    )

    assert validate_metadata(metadata).is_valid


def test_future_verification_date_rejected():

    metadata = RevisionMetadata(
        verified_date="2099-01-01"
    )

    result = validate_metadata(metadata)

    assert "verified_date" in get_error_paths(result)


def test_valid_source(source):

    metadata = RevisionMetadata(
        sources=(source,)
    )

    assert validate_metadata(metadata).is_valid


@pytest.mark.parametrize(
    "url",
    [
        "",
        "invalid",
        "ftp://example.com",
        "https://",
        "file:///tmp/rules",
        "https://user:password@example.com"
    ]
)
def test_invalid_source_url(source, url):

    metadata = RevisionMetadata(
        sources=(
            replace(source, url=url),
        )
    )

    result = validate_metadata(metadata)

    assert (
        "sources[0].url"
        in get_error_paths(result)
    )


def test_invalid_source_date(source):

    metadata = RevisionMetadata(
        sources=(
            replace(
                source,
                accessed_date="invalid"
            ),
        )
    )

    result = validate_metadata(metadata)

    assert (
        "sources[0].accessed_date"
        in get_error_paths(result)
    )


def test_unresolved_rule():

    metadata = RevisionMetadata(
        unresolved_rules=(
            UnresolvedRule(
                path="funded.payout",
                reason=UnresolvedReason.UNKNOWN,
                description="Requires research"
            ),
        )
    )

    assert validate_metadata(metadata).is_valid


def test_invalid_unresolved_reason():

    metadata = RevisionMetadata(
        unresolved_rules=(
            UnresolvedRule(
                path="funded.payout",
                reason="invalid",
                description="Requires research"
            ),
        )
    )

    result = validate_metadata(metadata)

    assert (
        "unresolved_rules[0].reason"
        in get_error_paths(result)
    )


def test_metadata_rejects_mutable_sources(source):

    metadata = RevisionMetadata(
        sources=[source]
    )

    assert not validate_metadata(metadata).is_valid


# ==================================================
# Revision validation and verification
# ==================================================

def test_valid_record(record):

    assert record.validate().is_valid


def test_invalid_firm_record(record):

    invalid = replace(
        record,
        firm=replace(record.firm, name="")
    )

    assert not invalid.validate().is_valid


def test_verified_requires_sources(record):

    verified = replace(
        record,
        firm=mark_verified(record.firm)
    )

    result = verified.validate()

    assert (
        "metadata.sources"
        in get_error_paths(result)
    )


def test_verified_requires_verification_date(
    record,
    source
):

    verified = replace(
        record,

        firm=mark_verified(record.firm),

        metadata=replace(
            record.metadata,
            sources=(source,)
        )
    )

    result = verified.validate()

    assert (
        "metadata.verified_date"
        in get_error_paths(result)
    )


def test_complete_verification(record, source):

    verified = replace(
        record,

        firm=mark_verified(record.firm),

        metadata=replace(
            record.metadata,
            verified_date="2026-01-01",
            sources=(source,)
        )
    )

    assert verified.validate().is_valid

    assert (
        verified.validate_for_simulation().is_valid
    )


def test_verified_cannot_have_unresolved_rules(
    record,
    source
):

    verified = replace(
        record,

        firm=mark_verified(record.firm),

        metadata=replace(
            record.metadata,

            verified_date="2026-01-01",

            sources=(source,),

            unresolved_rules=(
                UnresolvedRule(
                    path="funded.payout",
                    reason=UnresolvedReason.UNKNOWN,
                    description="Not confirmed"
                ),
            )
        )
    )

    result = verified.validate()

    assert (
        "metadata.unresolved_rules"
        in get_error_paths(result)
    )


def test_unverified_date_rejected(record):

    invalid = replace(
        record,

        metadata=replace(
            record.metadata,
            verified_date="2026-01-01"
        )
    )

    assert not invalid.validate().is_valid


def test_verified_simulation_required_by_default(
    record
):

    result = record.validate_for_simulation()

    assert not result.is_valid

    assert "rules_verified" in get_error_paths(result)


def test_hypothetical_simulation_allowed(record):

    result = record.validate_for_simulation(
        require_verified=False
    )

    assert result.is_valid


def test_unresolved_hypothetical_warning(record):

    draft = replace(
        record,

        metadata=replace(
            record.metadata,

            unresolved_rules=(
                UnresolvedRule(
                    path="funded.payout",
                    reason=UnresolvedReason.UNKNOWN,
                    description="Not researched"
                ),
            )
        )
    )

    result = draft.validate_for_simulation(
        require_verified=False
    )

    assert result.is_valid

    assert "metadata.unresolved_rules" in {
        warning.path
        for warning in result.warnings
    }


# ==================================================
# Serialization and fingerprints
# ==================================================

def test_revision_round_trip(record):

    restored = FirmRevision.from_json(
        record.to_json()
    )

    assert restored == record
    assert restored is not record


def test_nested_metadata_round_trip(record, source):

    original = replace(
        record,

        metadata=replace(
            record.metadata,

            sources=(source,),

            unresolved_rules=(
                UnresolvedRule(
                    path="funded.payout",
                    reason=UnresolvedReason.UNSUPPORTED,
                    description="Dynamic payout schedule"
                ),
            )
        )
    )

    restored = FirmRevision.from_json(
        original.to_json()
    )

    assert restored == original

    assert isinstance(
        restored.metadata.sources,
        tuple
    )

    assert (
        restored.metadata.unresolved_rules[0].reason
        is UnresolvedReason.UNSUPPORTED
    )


def test_invalid_revision_not_serialized(record):

    invalid = replace(
        record,

        metadata=replace(
            record.metadata,
            revision=0
        )
    )

    with pytest.raises(SerializationError):

        invalid.to_json()


def test_unknown_schema_version_not_loaded(record):

    data = json.loads(
        record.to_json()
    )

    data["metadata"]["schema_version"] = 999

    with pytest.raises(DeserializationError):

        FirmRevision.from_json(
            json.dumps(data)
        )


def test_fingerprint_length(record):

    assert len(record.fingerprint) == 64


def test_fingerprint_deterministic(record):

    assert record.fingerprint == record.fingerprint


def test_fingerprint_preserved_after_loading(record):

    restored = FirmRevision.from_json(
        record.to_json()
    )

    assert (
        restored.fingerprint
        == record.fingerprint
    )


def test_rule_change_alters_fingerprint(record):

    modified = replace(
        record,
        firm=update_fee(record.firm, 80)
    )

    assert (
        modified.rules_fingerprint
        != record.rules_fingerprint
    )

    assert (
        modified.fingerprint
        != record.fingerprint
    )


def test_metadata_change_preserves_rules_fingerprint(
    record
):

    modified = replace(
        record,

        metadata=replace(
            record.metadata,
            notes="Updated research notes"
        )
    )

    assert (
        modified.rules_fingerprint
        == record.rules_fingerprint
    )

    assert (
        modified.fingerprint
        != record.fingerprint
    )


def test_numeric_normalization(record):

    integer_fee = replace(
        record,
        firm=update_fee(record.firm, 100)
    )

    float_fee = replace(
        record,
        firm=update_fee(record.firm, 100.0)
    )

    assert (
        integer_fee.fingerprint
        == float_fee.fingerprint
    )


def test_verification_does_not_change_rules_hash(
    record,
    source
):

    verified = replace(
        record,

        firm=mark_verified(record.firm),

        metadata=replace(
            record.metadata,
            sources=(source,),
            verified_date="2026-01-01"
        )
    )

    assert (
        verified.rules_fingerprint
        == record.rules_fingerprint
    )

    assert (
        verified.fingerprint
        != record.fingerprint
    )


# ==================================================
# Database initialization and insertion
# ==================================================

def test_empty_database(database):

    assert database.list_ids() == []
    assert database.load_all() == {}
    assert len(database) == 0


def test_add_first_revision(database, firm):

    firm_id = database.add(firm)

    record = database.get_revision(firm_id)

    assert record.metadata.revision == 1
    assert record.firm == firm


def test_duplicate_firm_rejected(database, firm):

    database.add(firm)

    with pytest.raises(FirmAlreadyExistsError):

        database.add(firm)


def test_revision_number_assigned_by_database(
    database,
    firm
):

    firm_id = database.add(
        firm,
        RevisionMetadata(revision=99)
    )

    assert database.list_revisions(firm_id) == [1]


def test_missing_firm(database):

    with pytest.raises(FirmNotFoundError):

        database.get_revision("missing_firm")


def test_database_persists(database, firm):

    firm_id = database.add(firm)

    reopened = VersionedFirmDatabase(
        database.directory
    )

    assert reopened.get(firm_id) == firm


# ==================================================
# Revision history
# ==================================================

def test_append_revision(database, firm):

    firm_id = database.add(firm)

    revision = database.add_revision(
        firm_id,
        update_fee(firm, 80),
        expected_revision=1
    )

    assert revision == 2

    assert database.list_revisions(firm_id) == [
        1, 2
    ]


def test_original_revision_preserved(database, firm):

    firm_id = database.add(firm)

    original_path = (
        database.directory
        / firm_id
        / "revision_0001.json"
    )

    original_bytes = original_path.read_bytes()

    database.add_revision(
        firm_id,
        update_fee(firm, 80),
        expected_revision=1
    )

    assert original_path.read_bytes() == original_bytes

    assert database.get_revision(
        firm_id, 1
    ).firm == firm


def test_latest_revision_retrieval(database, firm):

    firm_id = database.add(firm)

    updated = update_fee(firm, 80)

    database.add_revision(
        firm_id,
        updated,
        expected_revision=1
    )

    latest = database.get_revision(firm_id)

    assert latest.metadata.revision == 2
    assert latest.firm == updated


def test_missing_revision(database, firm):

    firm_id = database.add(firm)

    with pytest.raises(RevisionNotFoundError):

        database.get_revision(firm_id, 99)


def test_stale_revision_rejected(database, firm):

    firm_id = database.add(firm)

    database.add_revision(
        firm_id,
        update_fee(firm, 80),
        expected_revision=1
    )

    with pytest.raises(RevisionConflictError):

        database.add_revision(
            firm_id,
            update_fee(firm, 60),
            expected_revision=1
        )

    assert database.list_revisions(firm_id) == [
        1, 2
    ]


def test_cannot_change_firm_identity(database, firm):

    firm_id = database.add(firm)

    renamed = replace(
        firm,
        name="Different Trading"
    )

    with pytest.raises(DatabaseError):

        database.add_revision(
            firm_id,
            renamed,
            expected_revision=1
        )


def test_updated_rules_reset_verification(
    database,
    firm,
    source
):

    verified = mark_verified(firm)

    firm_id = database.add(
        verified,

        RevisionMetadata(
            effective_date="2026-01-01",
            verified_date="2026-01-01",
            sources=(source,)
        )
    )

    updated = update_fee(verified, 80)

    database.add_revision(
        firm_id,
        updated,
        expected_revision=1
    )

    latest = database.get_revision(firm_id)

    assert not latest.firm.ruleset.rules_verified
    assert latest.metadata.verified_date is None

    original = database.get_revision(firm_id, 1)

    assert original.firm.ruleset.rules_verified


def test_unresolved_rules_preserved(database, firm):

    unresolved = UnresolvedRule(
        path="funded.payout",
        reason=UnresolvedReason.UNKNOWN,
        description="Requires research"
    )

    firm_id = database.add(
        firm,

        RevisionMetadata(
            unresolved_rules=(unresolved,)
        )
    )

    database.add_revision(
        firm_id,
        update_fee(firm, 80),
        expected_revision=1
    )

    latest = database.get_revision(firm_id)

    assert latest.metadata.unresolved_rules == (
        unresolved,
    )


def test_complete_history(database, firm):

    firm_id = database.add(firm)

    for revision, fee in enumerate(
        [80, 60, 120],
        start=1
    ):

        database.add_revision(
            firm_id,
            update_fee(firm, fee),
            expected_revision=revision
        )

    history = database.history(firm_id)

    assert [
        record.metadata.revision
        for record in history
    ] == [1, 2, 3, 4]


# ==================================================
# Effective-date retrieval
# ==================================================

def test_effective_revision(database, firm):

    firm_id = database.add(
        firm,

        RevisionMetadata(
            effective_date="2026-01-01"
        )
    )

    database.add_revision(
        firm_id,
        update_fee(firm, 80),

        RevisionMetadata(
            effective_date="2026-03-01"
        ),

        expected_revision=1
    )

    database.add_revision(
        firm_id,
        update_fee(firm, 120),

        RevisionMetadata(
            effective_date="2026-06-01"
        ),

        expected_revision=2
    )

    record = database.get_effective_revision(
        firm_id,
        date(2026, 4, 1)
    )

    assert record.metadata.revision == 2

    assert (
        record.firm.ruleset.evaluation.fees.purchase
        == 80
    )


def test_effective_date_boundary(database, firm):

    firm_id = database.add(
        firm,

        RevisionMetadata(
            effective_date="2026-01-01"
        )
    )

    record = database.get_effective_revision(
        firm_id,
        date(2026, 1, 1)
    )

    assert record.metadata.revision == 1


def test_no_applicable_effective_revision(
    database,
    firm
):

    firm_id = database.add(
        firm,

        RevisionMetadata(
            effective_date="2026-01-01"
        )
    )

    with pytest.raises(RevisionNotFoundError):

        database.get_effective_revision(
            firm_id,
            date(2025, 12, 31)
        )


def test_undated_revision_not_assumed_effective(
    database,
    firm
):

    firm_id = database.add(firm)

    with pytest.raises(RevisionNotFoundError):

        database.get_effective_revision(
            firm_id,
            date(2026, 11, 27)
        )


def test_same_effective_date_uses_newer_revision(
    database,
    firm
):

    metadata = RevisionMetadata(
        effective_date="2026-01-01"
    )

    firm_id = database.add(firm, metadata)

    database.add_revision(
        firm_id,
        update_fee(firm, 80),
        metadata,
        expected_revision=1
    )

    record = database.get_effective_revision(
        firm_id,
        date(2026, 2, 1)
    )

    assert record.metadata.revision == 2


# ==================================================
# Database integrity
# ==================================================

def test_corrupted_revision(database, firm):

    firm_id = database.add(firm)

    path = (
        database.directory
        / firm_id
        / "revision_0001.json"
    )

    path.write_text("{invalid")

    with pytest.raises(DatabaseEntryError):

        database.get_revision(firm_id)


def test_revision_number_mismatch(database, firm):

    firm_id = database.add(firm)

    path = (
        database.directory
        / firm_id
        / "revision_0001.json"
    )

    data = json.loads(path.read_text())

    data["metadata"]["revision"] = 2

    path.write_text(json.dumps(data))

    with pytest.raises(DatabaseEntryError):

        database.get_revision(firm_id)


def test_missing_revision_detected(database, firm):

    firm_id = database.add(firm)

    database.add_revision(
        firm_id,
        update_fee(firm, 80),
        expected_revision=1
    )

    original = (
        database.directory
        / firm_id
        / "revision_0001.json"
    )

    original.unlink()

    with pytest.raises(DatabaseEntryError):

        database.list_revisions(firm_id)


def test_failed_revision_publication(
    database,
    firm,
    monkeypatch
):

    firm_id = database.add(firm)

    def failed_link(*args, **kwargs):

        raise OSError("Simulated failure")

    monkeypatch.setattr(
        "openquant.propfirm.database.os.link",
        failed_link
    )

    with pytest.raises(OSError):

        database.add_revision(
            firm_id,
            update_fee(firm, 80),
            expected_revision=1
        )

    assert database.list_revisions(firm_id) == [1]

    assert database.get(firm_id) == firm


# ==================================================
# Compatible database interface
# ==================================================

def test_search(populated_database):

    results = populated_database.search(
        "example"
    )

    assert len(results) == 1


def test_filter(populated_database):

    results = populated_database.filter(
        lambda firm:
            firm.ruleset.account_size == 25_000
    )

    assert len(results) == 1


def test_membership(populated_database):

    assert (
        "example_trading_flex_25k"
        in populated_database
    )


def test_load_all_returns_prop_firms(
    populated_database
):

    assert all(
        isinstance(firm, PropFirm)

        for firm in
        populated_database.load_all().values()
    )


# ==================================================
# Legacy database migration
# ==================================================

def test_migrate_legacy_database(
    database,
    firm,
    tmp_path
):

    legacy = FirmDatabase(
        tmp_path / "legacy"
    )

    firm_id = legacy.add(firm)

    results = database.migrate_from(legacy)

    assert results[firm_id] == "migrated"
    assert database.get(firm_id) == firm
    assert legacy.get(firm_id) == firm


def test_migration_idempotent(
    database,
    firm,
    tmp_path
):

    legacy = FirmDatabase(
        tmp_path / "legacy"
    )

    firm_id = legacy.add(firm)

    database.migrate_from(legacy)

    results = database.migrate_from(legacy)

    assert results[firm_id] == "already_migrated"

    assert database.list_revisions(firm_id) == [1]


def test_migration_resets_old_verification(
    database,
    firm,
    tmp_path
):

    legacy = FirmDatabase(
        tmp_path / "legacy"
    )

    firm_id = legacy.add(
        mark_verified(firm)
    )

    database.migrate_from(legacy)

    restored = database.get(firm_id)

    assert not restored.ruleset.rules_verified

    assert legacy.get(
        firm_id
    ).ruleset.rules_verified


def test_migration_conflict(
    database,
    firm,
    tmp_path
):

    legacy = FirmDatabase(
        tmp_path / "legacy"
    )

    legacy.add(firm)

    database.add(
        update_fee(firm, 80)
    )

    with pytest.raises(RevisionConflictError):

        database.migrate_from(legacy)