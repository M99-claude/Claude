"""Načítanie YAML konfigurácie. Tajné údaje sa nikdy nepíšu do súboru, len názvy env premenných."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class AccountConfig:
    name: str  # alias, ktorý sa zapíše do stĺpca „Účet“
    provider: str  # "fio" | "enablebanking"
    options: dict = field(default_factory=dict)


@dataclass
class SheetConfig:
    spreadsheet_id: str
    worksheet: str = "Transakcie"
    # Jazykové nastavenie tabuľky – určuje desatinný oddeľovač (sk_SK = čiarka).
    locale: str = "sk_SK"


@dataclass
class Config:
    sheet: SheetConfig
    accounts: list[AccountConfig]
    timezone: str = "Europe/Bratislava"
    enablebanking: dict = field(default_factory=dict)


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Chýba premenná prostredia {name}")
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Konfiguračný súbor {path} neexistuje (skopírujte config.example.yaml)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    sheet_raw = raw.get("google_sheet") or {}
    spreadsheet_id = sheet_raw.get("spreadsheet_id") or os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ConfigError("Chýba google_sheet.spreadsheet_id (alebo env SPREADSHEET_ID)")
    sheet = SheetConfig(
        spreadsheet_id=spreadsheet_id,
        worksheet=sheet_raw.get("worksheet", "Transakcie"),
        locale=sheet_raw.get("locale", "sk_SK"),
    )

    accounts = []
    names = set()
    for item in raw.get("accounts") or []:
        item = dict(item)
        name = item.pop("name", None)
        provider = item.pop("provider", None)
        if not name or not provider:
            raise ConfigError(f"Účet musí mať 'name' aj 'provider': {item}")
        if name in names:
            raise ConfigError(f"Duplicitný názov účtu: {name}")
        names.add(name)
        accounts.append(AccountConfig(name=name, provider=provider, options=item))
    if not accounts:
        raise ConfigError("V konfigurácii nie je žiadny účet (sekcia accounts)")

    return Config(
        sheet=sheet,
        accounts=accounts,
        timezone=raw.get("timezone", "Europe/Bratislava"),
        enablebanking=raw.get("enablebanking") or {},
    )
