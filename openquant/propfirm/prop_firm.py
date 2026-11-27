from dataclasses import dataclass

from .ruleset import Ruleset

@dataclass(frozen=True)
class PropFirm:
    name: str
    ruleset: Ruleset

    def __str__(self) -> str:
        return f"{self.name} - {self.ruleset.account_name}"

    