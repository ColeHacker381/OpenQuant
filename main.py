from openquant.propfirm.prop_firm import PropFirm
from openquant.propfirm.ruleset import Ruleset

def main():

    ruleset = Ruleset(
        account_name="Flex 25k",
        account_size=25_000
    )

    firm = PropFirm(
        name="Example Firm",
        ruleset=ruleset
    )

    print(firm)
    print(firm.ruleset)

if __name__ == "__main__":
    main()