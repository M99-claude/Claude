"""Spoločný formát transakcie, do ktorého každý provider prekladá dáta z banky."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

# Poradie a názvy stĺpcov v Google Sheete.
COLUMNS = [
    "Dátum",
    "Účet protistrany",
    "Protistrana",
    "Suma",
    "VS",
    "Popis",
    "IBAN účtu",
    "ID transakcie",
    "Stiahnuté",
]

# Stĺpce zo staršej verzie, ktoré sa pri prestavbe hárku odstránia.
REMOVED_COLUMNS = ["Účet", "Mena", "KS", "SS", "Typ"]

# Stĺpec, podľa ktorého sa rozpoznávajú už zapísané transakcie.
KEY_COLUMN = "ID transakcie"
DATE_COLUMN = "Dátum"
IBAN_COLUMN = "IBAN účtu"
DATE_FORMAT = "dd.mm.yyyy"

# Google Sheets ukladá dátumy ako počet dní od 30.12.1899.
_SHEETS_EPOCH = date(1899, 12, 30)


def sheets_date(d: date) -> int:
    """Dátum ako číslo, ktoré Sheets zobrazí podľa formátu stĺpca (DD.MM.YYYY)."""
    return (d - _SHEETS_EPOCH).days


@dataclass(frozen=True)
class Transaction:
    account: str  # alias účtu z konfigurácie
    transaction_id: str  # ID pridelené bankou, unikátne v rámci účtu
    booking_date: date
    amount: Decimal  # záporná suma = odchádzajúca platba
    currency: str
    counterparty_name: str = ""
    counterparty_account: str = ""
    variable_symbol: str = ""
    constant_symbol: str = ""
    specific_symbol: str = ""
    description: str = ""
    type: str = ""
    account_iban: str = ""  # IBAN účtu, z ktorého sa transakcia stiahla
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> str:
        """Globálne unikátny kľúč (ID banky nemusí byť unikátne naprieč účtami)."""
        return f"{self.account}:{self.transaction_id}"

    def to_row(self) -> list:
        return [
            sheets_date(self.booking_date),
            self.counterparty_account,
            self.counterparty_name,
            float(self.amount),
            self.variable_symbol,
            self.description,
            self.account_iban,
            self.key,
            self.fetched_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        ]
