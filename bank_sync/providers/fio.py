"""Fio banka – jednoduché REST API s tokenom z internetbankingu.

Dokumentácia: https://www.fio.sk/docs/cz/API_Bankovnictvi.pdf
Token: Internetbanking → Nastavenia → API → Pridať token (stačí „len sledovanie účtu“).
Limit: jedna požiadavka na token za 30 sekúnd.
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal

import requests

from ..config import env
from ..models import Transaction

DEFAULT_BASE_URL = "https://fioapi.fio.cz/v1/rest"
RATE_LIMIT_WAIT = 31


def _col(tx: dict, n: int) -> str:
    column = tx.get(f"column{n}")
    if not column or column.get("value") is None:
        return ""
    return str(column["value"]).strip()


def parse_transaction(account: str, tx: dict) -> Transaction:
    counterparty_account = _col(tx, 2)
    bank_code = _col(tx, 3)
    if counterparty_account and bank_code:
        counterparty_account = f"{counterparty_account}/{bank_code}"

    # Popis: správa pre príjemcu, inak komentár, inak identifikácia.
    description = _col(tx, 16) or _col(tx, 25) or _col(tx, 7)

    return Transaction(
        account=account,
        transaction_id=_col(tx, 22),
        booking_date=date.fromisoformat(_col(tx, 0)[:10]),  # "2026-09-23+0200"
        amount=Decimal(str(tx["column1"]["value"])),
        currency=_col(tx, 14),
        counterparty_name=_col(tx, 10),
        counterparty_account=counterparty_account,
        variable_symbol=_col(tx, 5),
        constant_symbol=_col(tx, 4),
        specific_symbol=_col(tx, 6),
        description=description,
        type=_col(tx, 8),
    )


class FioProvider:
    def __init__(self, account: str, options: dict, session: requests.Session | None = None):
        self.account = account
        self.token = env(options.get("token_env", "FIO_TOKEN"))
        self.base_url = options.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self.http = session or requests.Session()

    def fetch(self, date_from: date, date_to: date) -> list[Transaction]:
        url = (
            f"{self.base_url}/periods/{self.token}/"
            f"{date_from.isoformat()}/{date_to.isoformat()}/transactions.json"
        )
        for attempt in range(3):
            resp = self.http.get(url, timeout=60)
            # 409 = prekročený limit 1 požiadavky / 30 s
            if resp.status_code == 409 and attempt < 2:
                time.sleep(RATE_LIMIT_WAIT)
                continue
            break
        if resp.status_code != 200:
            # URL obsahuje token, preto ho do chyby nedávame.
            raise RuntimeError(f"Fio API vrátilo HTTP {resp.status_code} pre účet {self.account}")

        tx_list = (resp.json().get("accountStatement", {}).get("transactionList") or {}).get(
            "transaction"
        ) or []
        return [parse_transaction(self.account, tx) for tx in tx_list]
