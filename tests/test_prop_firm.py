import pytest

from dataclasses import FrozenInstanceError

from propfirm.prop_firm import PropFirm
from propfirm.ruleset import Ruleset


def test_create_ruleset():

    ruleset = Ruleset(
        account_name="Flex 25K",
        account_size=25_000
    )

    assert ruleset.account_name == "Flex 25K"
    assert ruleset.account_size == 25_000


def test_create_prop_firm():

    ruleset = Ruleset("Flex 25K", 25_000)
    firm = PropFirm("Example Firm", ruleset)

    assert firm.name == "Example Firm"
    assert firm.ruleset == ruleset
    assert firm.ruleset.account_size == 25_000


def test_prop_firm_string():

    ruleset = Ruleset("Flex 25K", 25_000)
    firm = PropFirm("Example Firm", ruleset)

    assert str(firm) == "Example Firm - Flex 25K"


def test_ruleset_equality():

    ruleset1 = Ruleset("Flex 25K", 25_000)
    ruleset2 = Ruleset("Flex 25K", 25_000)

    assert ruleset1 == ruleset2


def test_ruleset_immutable():

    ruleset = Ruleset("Flex 25K", 25_000)

    with pytest.raises(FrozenInstanceError):
        ruleset.account_size = 50_000


def test_prop_firm_immutable():

    firm = PropFirm(
        "Example Firm",
        Ruleset("Flex 25K", 25_000)
    )

    with pytest.raises(FrozenInstanceError):
        firm.name = "Different Firm"