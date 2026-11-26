from dataclasses import dataclass

@dataclass(frozen=True)
class Ruleset:
    account_name: str
    account_size: int